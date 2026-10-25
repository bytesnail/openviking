#!/usr/bin/env python
"""OpenViking v0.5.2 多模态 embedding 协议适配器(设计 §3.2)。

只做一件事:OpenViking 发出的裸 typed-parts 多模态 `input` 与本机 llama.cpp
要求的 `[{"content": parts}]` 外壳之间的形态转换;纯文本、文本批处理与已包装
输入原样转发。仅用 Python 标准库,配合镜像内 venv Python 运行。

安全约束:只监听 loopback;不读取任何密钥;不记录请求正文、图片、向量、
Authorization 头或响应正文;转发并发上限受限,不排队无界请求。
"""
import json
import logging
import os
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("embedding_adapter")
LOG.setLevel(logging.INFO)  # 访问日志为 INFO;确保未配置 root handler 时仍可达

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18353
DEFAULT_UPSTREAM = "http://127.0.0.1:18351/v1/embeddings"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_BODY = 67108864  # 64 MiB
DEFAULT_MAX_CONCURRENT = 2
DEFAULT_FORWARD_WAIT = 30
READY_PROBE_TIMEOUT = 3


class RequestError(Exception):
    """请求被适配器拒绝;status/code/message 均为非敏感信息。"""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _is_text_part(part):
    return (
        isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def _is_image_part(part):
    return (
        isinstance(part, dict)
        and part.get("type") == "image_url"
        and isinstance(part.get("image_url"), dict)
        and isinstance(part["image_url"].get("url"), str)
    )


def _is_wrapped(part):
    return isinstance(part, dict) and isinstance(part.get("content"), list)


def transform_body(body):
    """按 §3.2 分类并转换请求体;返回 (新请求体, 是否为多模态包装)。

    拒绝(RequestError):非对象、缺 input、input 非字符串/列表、空列表、
    混合形态、含不支持 part 类型或字段非法。不猜测转换。
    """
    if not isinstance(body, dict):
        raise RequestError(400, "invalid_request", "request body must be a JSON object")
    if "input" not in body:
        raise RequestError(400, "invalid_request", "missing required field: input")
    inp = body["input"]

    if isinstance(inp, str):
        return body, False
    if not isinstance(inp, list) or not inp:
        raise RequestError(400, "unsupported_input", "input must be a non-empty string or list")

    if all(isinstance(x, str) for x in inp):
        return body, False
    if all(_is_wrapped(x) for x in inp):
        return body, False
    if all(_is_text_part(x) or _is_image_part(x) for x in inp):
        return {**body, "input": [{"content": inp}]}, True
    raise RequestError(
        400,
        "unsupported_input",
        "input mixes shapes or contains unsupported part types",
    )


class AdapterServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, upstream_url, upstream_timeout_seconds,
                 max_body_bytes, max_concurrent, forward_wait_seconds):
        super().__init__(address, AdapterHandler)
        self.upstream_url = upstream_url
        self.upstream_health_url = _health_url(upstream_url)
        self.upstream_timeout_seconds = upstream_timeout_seconds
        self.max_body_bytes = max_body_bytes
        self.forward_slots = threading.BoundedSemaphore(max_concurrent)
        self.forward_wait_seconds = forward_wait_seconds


def _health_url(upstream_url):
    parts = urllib.parse.urlsplit(upstream_url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


class AdapterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---- 响应工具:统一 JSON 输出,错误信息一律通用化,不回显上游正文 ----

    def _send_json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, status, code, message):
        self._send_json(status, {"error": {"message": message, "type": code}})

    def log_request(self, code="-", size="-"):
        LOG.info("%s %s -> %s", self.command, self.path, code)  # 只记方法/路径/状态

    def log_message(self, fmt, *args):
        pass  # 框架其他日志静音,避免默认格式把请求行/地址混入

    # ---- 路由 ----

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/ready":
            if self._upstream_healthy():
                self._send_json(200, {"status": "ready", "upstream": "ok"})
            else:
                self._send_json(503, {"status": "unavailable", "upstream": "down"})
            return
        if self.path == "/v1/embeddings":
            self._send_error_json(405, "method_not_allowed", "use POST")
            return
        self._send_error_json(404, "not_found", "unknown path")

    def do_POST(self):
        if self.path != "/v1/embeddings":
            self._send_error_json(404, "not_found", "unknown path")
            return
        try:
            self._handle_embeddings()
        except RequestError as exc:
            LOG.warning("rejected status=%d code=%s", exc.status, exc.code)
            if exc.status in (411, 413):
                self.close_connection = True  # 未读完请求体,防止连接错位
            self._send_error_json(exc.status, exc.code, exc.message)
        except Exception:
            LOG.exception("internal_error")
            self._send_error_json(500, "internal_error", "adapter internal error")

    # ---- 核心转发 ----

    def _handle_embeddings(self):
        raw = self._read_body()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise RequestError(400, "invalid_json", "request body is not valid JSON")

        out_body, wrapped = transform_body(body)
        payload = json.dumps(out_body).encode() if wrapped else raw

        # 超出并发上限的请求只做有界等待,等待超时直接拒绝,不排无界队列
        if not self._acquire_slot():
            raise RequestError(503, "overloaded", "adapter concurrency limit reached")
        try:
            upstream_raw, upstream_status = self._call_upstream(payload)
        finally:
            self.server.forward_slots.release()

        if upstream_status != 200:
            LOG.warning("upstream failure status=%d", upstream_status)
            raise RequestError(502, "upstream_error", "embedding upstream failed")

        if wrapped:
            try:
                parsed = json.loads(upstream_raw)
                vectors = parsed.get("data")
            except ValueError:
                vectors = None
            if not isinstance(vectors, list) or len(vectors) != 1:
                LOG.warning("multimodal vector count invalid")
                raise RequestError(
                    502, "upstream_error", "multimodal response must contain exactly one vector"
                )
            upstream_raw = json.dumps(parsed).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(upstream_raw)))
        self.end_headers()
        self.wfile.write(upstream_raw)

    def _acquire_slot(self):
        return self.server.forward_slots.acquire(timeout=self.server.forward_wait_seconds)

    def _read_body(self):
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise RequestError(411, "length_required", "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError:
            raise RequestError(400, "invalid_request", "invalid Content-Length")
        if length < 0 or length > self.server.max_body_bytes:
            raise RequestError(413, "payload_too_large", "request body exceeds limit")
        return self.rfile.read(length)

    def _call_upstream(self, payload):
        """POST 到上游;任何失败折叠为 (None, 非 200 状态)。日志只记状态。"""
        req = urllib.request.Request(
            self.server.upstream_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.server.upstream_timeout_seconds
            ) as resp:
                return resp.read(), resp.status
        except urllib.error.HTTPError as err:
            err.close()
            return None, err.code
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            return None, 0  # 0 表示连接层失败,统一按上游失败处理

    def _upstream_healthy(self):
        try:
            with urllib.request.urlopen(
                self.server.upstream_health_url, timeout=READY_PROBE_TIMEOUT
            ) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False


def make_server(host=DEFAULT_HOST, port=DEFAULT_PORT, upstream_url=DEFAULT_UPSTREAM,
                upstream_timeout_seconds=DEFAULT_TIMEOUT,
                max_body_bytes=DEFAULT_MAX_BODY,
                max_concurrent=DEFAULT_MAX_CONCURRENT,
                forward_wait_seconds=DEFAULT_FORWARD_WAIT):
    return AdapterServer(
        (host, port), upstream_url, upstream_timeout_seconds,
        max_body_bytes, max_concurrent, forward_wait_seconds,
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    server = make_server(
        host=os.environ.get("EMBEDDING_ADAPTER_HOST", DEFAULT_HOST),
        port=int(os.environ.get("EMBEDDING_ADAPTER_PORT", DEFAULT_PORT)),
        upstream_url=os.environ.get("EMBEDDING_UPSTREAM_URL", DEFAULT_UPSTREAM),
        upstream_timeout_seconds=int(
            os.environ.get("EMBEDDING_UPSTREAM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
        ),
        max_body_bytes=int(os.environ.get("EMBEDDING_MAX_BODY_BYTES", DEFAULT_MAX_BODY)),
        max_concurrent=int(os.environ.get("EMBEDDING_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT)),
    )
    LOG.info(
        "listening host=%s port=%d upstream=%s",
        server.server_address[0], server.server_address[1], server.upstream_url,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
