#!/usr/bin/env python
"""OpenViking v0.5.2 多模态 embedding 协议适配器(设计 §3.2)。

只做一件事:OpenViking 发出的裸 typed-parts 多模态 `input` 与本机 llama.cpp
要求的 `[{"content": parts}]` 外壳之间的形态转换;纯文本、文本批处理与单项
已包装输入原样转发(多项 wrapped 批量输入按"恰好一个向量"契约以 400 拒绝)。
仅用 Python 标准库,配合镜像内 venv Python 运行。

安全约束:只监听 loopback;不读取任何密钥;不记录请求正文、图片、向量、
Authorization 头或响应正文;转发并发上限受限,不排队无界请求。
"""
import http.client
import json
import logging
import math
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("embedding_adapter")
LOG.setLevel(logging.INFO)  # 访问日志为 INFO;确保未配置 root handler 时仍可达
# 模型请求与 readiness 均只访问本机,禁止继承宿主代理发送文本/图片。
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向。

    urllib 默认会把 301/302/303 的 POST 转 GET 追到 Location 目标(GET
    路径则原样跟随)。上游只允许是固定 loopback 地址,绝不允许任何 3xx
    把请求(或探测)引去别处。返回 None 使 opener 以 HTTPError(3xx) 报错。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect()
)
# 同型 _NoRedirect/_DIRECT_OPENER 在 scripts/env_file.py 与
# scripts/bootstrap_auth.py 各有一份:保持 -I 下零 sys.path 操作、脚本自包含
# 是有意设计(显式 sys.path.insert 本可共享,先例 tests/e2e_adapter_probe.py)。
# 共享化已定为触发式(2026-11-07 用户决策):下次 opener/传输层加固时建
# scripts/_direct_http.py 单一来源(并收编散于本文件与 smoke_models 的
# 有限数谓词),触发器→范围映射见项目卡"统一视角"。在此之前,对此处的
# 任何加固必须同步另外两份,tests/test_env_file.py 有行为一致性断言。

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18353
# 端口落点之一;拓扑权威表与变更同步点清单见 README"端口拓扑与同步点"。
DEFAULT_UPSTREAM = "http://127.0.0.1:18351/v1/embeddings"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_BODY = 67108864  # 64 MiB
DEFAULT_MAX_RESPONSE_BODY = 67108864  # 上游 200 响应同样必须有界
DEFAULT_MAX_CONCURRENT = 2
DEFAULT_MAX_CONNECTIONS = 16
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


def _is_wrapped_input(inp):
    return isinstance(inp, list) and bool(inp) and all(_is_wrapped(x) for x in inp)


def single_vector(body):
    """返回恰好一个有限、非零向量；其他响应一律返回 None。"""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    if len(body["data"]) != 1 or not isinstance(body["data"][0], dict):
        return None
    vector = body["data"][0].get("embedding")
    if not isinstance(vector, list) or not vector:
        return None
    # 同型"有限数值"判定在 scripts/smoke_models.py(_valid_rerank_scores)与
    # scripts/bootstrap_auth.py(_ensure_timeout_file)各有一份(-I 隔离运行);
    # 修改溢出/布尔排除等语义须三处同步。
    try:
        finite = all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            for x in vector
        )
    except OverflowError:
        # JSON 可表达任意精度整数;超出 float 范围按无效向量处理而非 500。
        return None
    if not finite:
        return None
    if not any(x != 0 for x in vector):
        return None  # 全零向量无法归一化,余弦相似度未定义
    return vector


def _upstream_log_origin(url):
    """日志只保留上游 origin,绝不输出 userinfo、路径或查询参数。"""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port is not None else "")
        return urllib.parse.urlunsplit((parts.scheme, netloc, "", "", ""))
    except ValueError:
        return "invalid-upstream-url"


def transform_body(body):
    """按 §3.2 分类并转换请求体;返回 (新请求体, 是否需校验单向量契约)。

    需校验单向量:裸 parts 多模态与单项已包装输入(上游必须恰好返回一个
    向量);纯文本与文本批处理不需要。passthrough 路径原样返回 body 本体,
    调用方按对象身份区分"未改写"与"已包装",不再二次推导分类
    (2026-11-03 检视)。
    拒绝(RequestError):非对象、缺 input、input 非字符串/列表、空列表、
    混合形态、含不支持 part 类型或字段非法、多项 wrapped 批量输入。
    不猜测转换。
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
    if _is_wrapped_input(inp):
        # 多项 wrapped 批量输入会让上游返回 N 个向量,而本适配器的多模态
        # 契约是"恰好一个向量"——原样转发只会把整批推理结果 502 掉
        # (ov.conf embedding.max_retries 还会放大成 N×3 次推理),在
        # 转换阶段就以 400 拒绝,不猜测转换。
        if len(inp) != 1:
            raise RequestError(
                400,
                "unsupported_input",
                "wrapped input must contain exactly one item",
            )
        # 空 content 与裸空 parts 列表同一语义:就地 400 拒绝,不浪费一次
        # 上游推理再把它 502 掉(与上方空列表拒绝对称)。
        if not inp[0].get("content"):
            raise RequestError(
                400,
                "unsupported_input",
                "wrapped input content must not be empty",
            )
        return body, True
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
                 max_body_bytes, max_concurrent, forward_wait_seconds,
                 max_response_body_bytes=DEFAULT_MAX_RESPONSE_BODY,
                 max_connections=DEFAULT_MAX_CONNECTIONS):
        super().__init__(address, AdapterHandler)
        self.upstream_url = upstream_url
        self.upstream_health_url = _health_url(upstream_url)
        self.upstream_timeout_seconds = upstream_timeout_seconds
        self.max_body_bytes = max_body_bytes
        self.max_response_body_bytes = max_response_body_bytes
        self.forward_slots = threading.BoundedSemaphore(max_concurrent)
        self.forward_wait_seconds = forward_wait_seconds
        # ThreadingHTTPServer 默认每连接一个线程;在 accept 主循环中先取连接
        # 额度,才能把慢 header 连接也纳入硬上限。listen backlog 不是线程上限。
        self.connection_slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        self.connection_slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def _health_url(upstream_url):
    parts = urllib.parse.urlsplit(upstream_url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


class AdapterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # headers 与 body 分两次写;不关 Nagle 会在 loopback keep-alive 上触发
    # delayed-ACK,每个响应多约 40ms(实测;socketserver 会设 TCP_NODELAY)。
    disable_nagle_algorithm = True
    # 入站 socket read 与整体正文 deadline 共用此秒数;每次 read 前按剩余
    # 时间重设 timeout,慢速合法请求也不能靠逐字节慢滴永久占用额度。
    timeout = 120
    # 已读入的声明正文字节数(finish 时有界排水用;见 _drain_unread_body)。
    _body_bytes_read = 0

    # ---- 响应工具:统一 JSON 输出,错误信息一律通用化,不回显上游正文 ----

    def _send_json(self, status, obj, close=False):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if close:
            # 服务端确定不复用连接时必须向客户端声明;否则协议正确的
            # keep-alive 客户端有权复用连接,复用请求只会撞上单方面关闭。
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, status, code, message):
        # 错误路径可能尚未读完请求体;统一关闭,防止 keep-alive 请求错位。
        self.close_connection = True
        self._send_json(status, {"error": {"message": message, "type": code}},
                        close=True)

    def log_request(self, code="-", size="-"):
        # 空连接/坏请求行可能在 command/path 赋值前触发日志。查询串属于请求
        # 内容(可能携带凭据),日志只保留去查询串的路径(2026-11-03 检视)。
        LOG.info(
            "%s %s -> %s",
            getattr(self, "command", "-"),
            getattr(self, "path", "-").split("?", 1)[0],
            code,
        )

    def log_message(self, fmt, *args):
        pass  # 框架其他日志静音,避免默认格式把请求行/地址混入

    # ---- 路由 ----

    def handle(self):
        try:
            super().handle()
        except (TimeoutError, socket.timeout, BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):
            # socket.timeout 在 3.10+ 即 TimeoutError 的别名;显式并列以与
            # _read_request_body/_call_upstream 的防御写法一致,不依赖镜像
            # Python 版本。
            # 超时/错误响应后客户端可立即断开;残留字节或后续写响应(含对已
            # 关闭 socket 的写超时)不应让 socketserver 打 traceback,也不
            # 影响其他连接。
            self.close_connection = True

    def do_GET(self):
        # 同一 handler 实例服务一条 keep-alive 连接上的所有请求;逐请求状态
        # 必须在入口重置,否则上一请求的已读字节数/响应开始标记会污染下一
        # 请求的排水计算与错误兜底分类。
        self._body_bytes_read = 0
        self._response_started = False
        # GET 也可能带体;成功响应前不读体,因此统一禁止连接复用
        # (close=True 向客户端显式声明,防复用撞上单方面关闭)。
        self.close_connection = True
        if self.path == "/health":
            self._send_json(200, {"status": "ok"}, close=True)
            return
        if self.path == "/ready":
            if self._upstream_healthy():
                self._send_json(200, {"status": "ready", "upstream": "ok"}, close=True)
            else:
                self._send_json(
                    503, {"status": "unavailable", "upstream": "down"}, close=True
                )
            return
        if self.path == "/v1/embeddings":
            self._send_error_json(405, "method_not_allowed", "use POST")
            return
        self._send_error_json(404, "not_found", "unknown path")

    def do_POST(self):
        # 逐请求状态重置(见 do_GET):上一请求残留会污染排水与兜底分类。
        self._body_bytes_read = 0
        self._response_started = False
        if self.path != "/v1/embeddings":
            self.close_connection = True  # 不读未知路径的请求体,关闭连接防止 keep-alive 错位
            self._send_error_json(404, "not_found", "unknown path")
            return
        try:
            self._handle_embeddings()
        except RequestError as exc:
            LOG.warning("rejected status=%d code=%s", exc.status, exc.code)
            self._send_error_json(exc.status, exc.code, exc.message)
        except Exception:
            # 响应已开始写出后绝不能再写第二个状态行(客户端会收到 200 头 +
            # 截断正文 + 内嵌 500 块的损坏流);只能记录并关闭连接
            # (2026-11-03 检视)。
            if getattr(self, "_response_started", False):
                LOG.exception("internal_error_after_response_started")
                self.close_connection = True
                return
            LOG.exception("internal_error")
            self._send_error_json(500, "internal_error", "adapter internal error")

    # ---- 核心转发 ----

    def _handle_embeddings(self):
        # 先校验长度并占用并发额度,再读正文:并发上限同时约束"已缓存正文"的
        # 总内存(而非仅转发数),超限请求不读体直接拒绝,不排无界队列
        length = self._content_length()
        if not self._acquire_slot():
            self.close_connection = True  # 正文未读,关闭连接防止 keep-alive 错位
            raise RequestError(503, "overloaded", "adapter concurrency limit reached")
        try:
            raw = self._read_request_body(length)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise RequestError(400, "invalid_json", "request body is not valid JSON")

            out_body, needs_single_vector = transform_body(body)
            # passthrough 路径原样返回 body 本体:按对象身份选择转发字节,
            # 免二次序列化;输入分类只此一处,调用点不再重新推导。
            payload = raw if out_body is body else json.dumps(out_body).encode()
            # 上游推理期间只保留实际发送的字节,避免原始字节、JSON 对象图和
            # 重序列化结果在整个网络调用中同时存活。
            del raw, body, out_body
            upstream_raw, upstream_status = self._call_upstream(payload)
        finally:
            self.server.forward_slots.release()

        if upstream_status != 200:
            LOG.warning("upstream failure status=%d", upstream_status)
            status = upstream_status if 400 <= upstream_status < 500 else 502
            raise RequestError(status, "upstream_error", "embedding upstream failed")

        if needs_single_vector:
            try:
                parsed = json.loads(upstream_raw)
            except ValueError:
                parsed = None
            if single_vector(parsed) is None:
                LOG.warning("multimodal vector response invalid")
                raise RequestError(
                    502, "upstream_error", "multimodal response must contain exactly one valid vector"
                )
            # 校验通过后原样转发上游字节,不再解析-重序列化(保真且省一遍开销)

        # 标记响应已开始:此后任何异常都不得再写第二份状态行(见 do_POST)。
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(upstream_raw)))
        self.end_headers()
        self.wfile.write(upstream_raw)

    def _read_request_body(self, length):
        """在总 deadline 内读完声明长度,防止逐字节慢滴永久占用转发额度。"""
        raw = bytearray(length)
        view = memoryview(raw)
        offset = 0
        deadline = time.monotonic() + self.timeout
        try:
            while offset < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestError(
                        408, "request_timeout", "timed out reading request body"
                    )
                self.connection.settimeout(remaining)
                try:
                    # BufferedReader.readinto 可能为填满整个 slice 连续做多次
                    # raw read;read1 至多触发一次 raw read,才能在每批字节
                    # 到达后重新计算整体 deadline。
                    chunk = self.rfile.read1(min(1024 * 1024, length - offset))
                except (socket.timeout, TimeoutError):
                    raise RequestError(
                        408, "request_timeout", "timed out reading request body"
                    )
                if not chunk:
                    break
                view[offset:offset + len(chunk)] = chunk
                offset += len(chunk)
        finally:
            view.release()
            self._body_bytes_read = offset
            # read1 前按剩余 deadline 缩短了 socket timeout;完整读取后必须
            # 恢复,否则 keep-alive 下一请求会继承接近 0 的 header 超时。
            self.connection.settimeout(self.timeout)
        if offset != length:
            raise RequestError(400, "invalid_request", "incomplete request body")
        return raw

    def finish(self):
        # 拒绝路径未读请求体就关闭:接收缓冲留有未读字节时,内核会以 RST
        # 代替 FIN 关闭,已写出的错误响应可能被客户端整包丢弃(上层只见
        # "连接被重置"而非 413/503)。有界排水排掉常见小残留;超大流式
        # 正文仍有 RST 残余,属可接受边界(2026-11-03 检视)。
        if self.close_connection:
            self._drain_unread_body()
        super().finish()

    def _drain_unread_body(self):
        headers = getattr(self, "headers", None)
        if headers is None:
            return
        try:
            length_headers = headers.get_all("Content-Length") or []
            if len(length_headers) != 1:
                return
            header = length_headers[0]
            if not (header.isascii() and header.isdigit()):
                return
            remaining = max(0, int(header) - self._body_bytes_read)
            if not remaining:
                return
            cap = min(remaining, 262144)  # 至多 256 KiB / ~1s:不为恶意大正文买单
            deadline = time.monotonic() + 1.0
            self.connection.settimeout(1.0)
            while cap > 0 and time.monotonic() < deadline:
                # 经 rfile 排水而非裸 socket:小请求的头+体常已被读头的
                # 缓冲读整体吞进 rfile 用户态缓冲,裸 recv 会扑空并空等满
                # 1s 超时;rfile.read1 先吐缓冲存量,真正无数据才等超时。
                chunk = self.rfile.read1(min(65536, cap))
                if not chunk:
                    break
                cap -= len(chunk)
        except OSError:
            pass

    def _acquire_slot(self):
        return self.server.forward_slots.acquire(timeout=self.server.forward_wait_seconds)

    def _content_length(self):
        """只校验 Content-Length 头并返回长度;任何拒绝路径都标记关闭连接
        (此刻请求体尚未读,留着会在 keep-alive 连接上错位下一个请求)。"""
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            raise RequestError(400, "invalid_request", "Transfer-Encoding is not supported")
        length_headers = self.headers.get_all("Content-Length") or []
        if not length_headers:
            self.close_connection = True
            raise RequestError(411, "length_required", "Content-Length is required")
        if len(length_headers) != 1:
            self.close_connection = True
            raise RequestError(400, "invalid_request", "exactly one Content-Length is required")
        length_header = length_headers[0]
        # RFC 9110 只允许纯 ASCII 数字;int() 还会接受 "+5"/" 5"/Unicode
        # 十进制数字/下划线分组,必须收紧到与严格头语法一致(2026-11-03 检视)。
        if not (length_header.isascii() and length_header.isdigit()):
            self.close_connection = True
            raise RequestError(400, "invalid_request", "invalid Content-Length")
        length = int(length_header)
        # isascii+isdigit 已排除负号,无需再判 length < 0。
        if length > self.server.max_body_bytes:
            self.close_connection = True
            raise RequestError(413, "payload_too_large", "request body exceeds limit")
        return length

    def _call_upstream(self, payload):
        """POST 到上游;任何失败折叠为 (None, 非 200 状态)。日志只记状态。"""
        req = urllib.request.Request(
            self.server.upstream_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _DIRECT_OPENER.open(
                req, timeout=self.server.upstream_timeout_seconds
            ) as resp:
                raw = resp.read(self.server.max_response_body_bytes + 1)
                if len(raw) > self.server.max_response_body_bytes:
                    raise RequestError(
                        502, "upstream_error", "embedding upstream response exceeds limit"
                    )
                return raw, resp.status
        except urllib.error.HTTPError as err:
            err.close()
            return None, err.code
        except (urllib.error.URLError, http.client.HTTPException,
                socket.timeout, TimeoutError, OSError):
            # HTTPException(如 IncompleteRead/BadStatusLine)是上游传输层失败,
            # 与连接层失败同样折叠为非 200,按上游失败处理而非适配器内部错误
            return None, 0  # 0 表示连接层失败,统一按上游失败处理

    def _upstream_healthy(self):
        try:
            with _DIRECT_OPENER.open(
                self.server.upstream_health_url, timeout=READY_PROBE_TIMEOUT
            ) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False


def make_server(host=DEFAULT_HOST, port=DEFAULT_PORT, upstream_url=DEFAULT_UPSTREAM,
                upstream_timeout_seconds=DEFAULT_TIMEOUT,
                max_body_bytes=DEFAULT_MAX_BODY,
                max_concurrent=DEFAULT_MAX_CONCURRENT,
                forward_wait_seconds=DEFAULT_FORWARD_WAIT,
                max_response_body_bytes=DEFAULT_MAX_RESPONSE_BODY,
                max_connections=DEFAULT_MAX_CONNECTIONS):
    return AdapterServer(
        (host, port), upstream_url, upstream_timeout_seconds,
        max_body_bytes, max_concurrent, forward_wait_seconds,
        max_response_body_bytes, max_connections,
    )


def _env_int(name, default, minimum=1):
    """读取整数环境变量;非法值或缺下界立即报错退出。

    不校验的后果:非整数以裸 traceback 崩溃并进入 restart 循环;
    EMBEDDING_MAX_CONNECTIONS=0 会让 BoundedSemaphore(0) 把 accept 主循环
    在首个连接上永久阻塞(静默无响应),两者都必须 fail loud。
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f"配置错误:{name} 必须是整数,当前值无法解析"
        ) from None
    if value < minimum:
        raise SystemExit(f"配置错误:{name} 必须 >= {minimum}")
    return value


def _loopback_host(host):
    """宿主地址必须显式是 IPv4 loopback:模块安全约束是"只监听 loopback",
    容器为 host 网络且适配器无认证,误绑 0.0.0.0 会把无鉴权代理暴露到局域网,
    必须 fail loud 而非静默照绑。不支持 ::1:ThreadingHTTPServer 默认
    AF_INET,接受 ::1 只会在 bind 处抛裸 gaierror,而非受控配置错误。"""
    resolved = {"localhost": "127.0.0.1"}.get(host, host)
    if resolved not in ("127.0.0.1",):
        raise SystemExit(
            f"配置错误:EMBEDDING_ADAPTER_HOST={host} 不是 IPv4 loopback;"
            "本适配器只允许绑定 127.0.0.1/localhost(不支持 ::1)"
        )
    return resolved


def _loopback_upstream_url(url):
    """上游也必须显式是 loopback:请求正文(用户文本/图片)绝不允许被一次
    环境变量误配(EMBEDDING_UPSTREAM_URL 指到远端)静默带出本机,必须
    与监听地址同一防线,启动即 fail loud。"""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        raise SystemExit(
            f"配置错误:EMBEDDING_UPSTREAM_URL={url} 无法解析"
        ) from None
    resolved = {"localhost": "127.0.0.1"}.get(host, host)
    if resolved not in ("127.0.0.1",):
        raise SystemExit(
            f"配置错误:EMBEDDING_UPSTREAM_URL={url} 的主机不是 IPv4 loopback;"
            "上游只允许 127.0.0.1/localhost"
        )
    return url


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    server = make_server(
        host=_loopback_host(os.environ.get("EMBEDDING_ADAPTER_HOST", DEFAULT_HOST)),
        port=_env_int("EMBEDDING_ADAPTER_PORT", DEFAULT_PORT),
        upstream_url=_loopback_upstream_url(
            os.environ.get("EMBEDDING_UPSTREAM_URL", DEFAULT_UPSTREAM)
        ),
        upstream_timeout_seconds=_env_int(
            "EMBEDDING_UPSTREAM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT
        ),
        max_body_bytes=_env_int("EMBEDDING_MAX_BODY_BYTES", DEFAULT_MAX_BODY),
        max_concurrent=_env_int("EMBEDDING_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT),
        max_response_body_bytes=_env_int(
            "EMBEDDING_MAX_RESPONSE_BODY_BYTES", DEFAULT_MAX_RESPONSE_BODY
        ),
        max_connections=_env_int("EMBEDDING_MAX_CONNECTIONS", DEFAULT_MAX_CONNECTIONS),
    )
    LOG.info(
        "listening host=%s port=%d upstream=%s",
        server.server_address[0], server.server_address[1],
        _upstream_log_origin(server.upstream_url),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
