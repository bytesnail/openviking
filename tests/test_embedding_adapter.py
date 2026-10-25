"""embedding_adapter.py 单元测试(设计 §9.1.3)。

覆盖:纯文本/文本批处理不变;OpenViking 多模态 parts 只加一层 content 包装;
已包装输入不重复包装;非法 part、混合输入、超大输入被拒绝;多模态多向量/空向量
响应返回 502;上游状态与错误安全处理,日志不含正文。
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import embedding_adapter as ea


# ---------- 测试工具:可编程的假上游(本地 llama.cpp 替身,§9.2 再做真实联调) ----------


class FakeUpstream:
    """记录请求、可编程响应的本地 /v1/embeddings + /health 假上游。"""

    def __init__(self):
        self.requests = []          # [(method, path, headers, body_bytes)]
        self.response_status = 200
        self.response_body = {"data": [{"embedding": [0.1] * 768, "index": 0}]}
        self.hang_seconds = 0.0
        self._inflight = 0
        self.max_inflight_seen = 0
        self._lock = threading.Lock()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with upstream._lock:
                    upstream.requests.append(
                        (self.command, self.path, dict(self.headers), body)
                    )
                if upstream.hang_seconds:
                    time.sleep(upstream.hang_seconds)
                with upstream._lock:
                    upstream._inflight += 1
                    upstream.max_inflight_seen = max(
                        upstream.max_inflight_seen, upstream._inflight
                    )
                try:
                    if self.path == "/health":
                        self._respond(200, {"status": "ok"})
                        return
                    status = upstream.response_status
                    if status == 200:
                        raw = json.dumps(upstream.response_body).encode()
                    else:
                        raw = json.dumps({"error": "secret-upstream-detail"}).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                finally:
                    with upstream._lock:
                        upstream._inflight -= 1

            def _respond(self, status, obj):
                raw = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = _handle
            do_POST = _handle

            def log_message(self, *args):  # 静音假上游日志
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1/embeddings"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def upstream():
    fake = FakeUpstream()
    yield fake
    fake.close()


@pytest.fixture()
def adapter(upstream):
    server = ea.make_server(
        host="127.0.0.1",
        port=0,
        upstream_url=upstream.url,
        upstream_timeout_seconds=5,
        max_body_bytes=4096,
        max_concurrent=2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


def http_post(base, path, obj, headers=None, raw_body=None):
    data = raw_body if raw_body is not None else json.dumps(obj).encode()
    req = urllib.request.Request(
        base + path, data=data, method="POST", headers=headers or {}
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def http_get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


TEXT_PART = {"type": "text", "text": "a red square"}
IMAGE_PART = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
}


def mm_body():
    return {
        "model": "embeddinggemma-2",
        "encoding_format": "float",
        "input": [dict(TEXT_PART), dict(IMAGE_PART)],
    }


# ---------- 纯函数:输入分类与包装(§3.2 职责 2/3/4) ----------


def test_text_string_forwarded_unchanged():
    body = {"model": "m", "input": "hello"}
    out, wrapped = ea.transform_body(body)
    assert out == body and wrapped is False


def test_text_batch_forwarded_unchanged():
    body = {"model": "m", "input": ["a", "b"]}
    out, wrapped = ea.transform_body(body)
    assert out == body and wrapped is False


def test_openviking_parts_get_content_wrapper():
    body = mm_body()
    out, wrapped = ea.transform_body(body)
    assert wrapped is True
    assert out["input"] == [{"content": body["input"]}]
    assert out["model"] == "embeddinggemma-2"  # 其余字段保留
    assert out["encoding_format"] == "float"


def test_already_wrapped_input_not_double_wrapped():
    body = {"model": "m", "input": [{"content": [dict(TEXT_PART), dict(IMAGE_PART)]}]}
    out, wrapped = ea.transform_body(body)
    assert out == body and wrapped is False


def test_unsupported_part_type_rejected():
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": [{"type": "audio", "audio": {"url": "x"}}]})


def test_part_missing_or_wrong_field_rejected():
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": [{"type": "text"}]})  # 缺 text
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": [{"type": "image_url", "image_url": "not-dict"}]})
    with pytest.raises(ea.RequestError):
        ea.transform_body(
            {"input": [{"type": "image_url", "image_url": {"url": 123}}]}
        )


def test_mixed_shapes_rejected():
    with pytest.raises(ea.RequestError):  # 字符串与 part 混排
        ea.transform_body({"input": ["text", dict(TEXT_PART)]})
    with pytest.raises(ea.RequestError):  # 已包装与裸 part 混排
        ea.transform_body(
            {"input": [{"content": [dict(TEXT_PART)]}, dict(IMAGE_PART)]}
        )


def test_empty_parts_list_rejected():
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": []})


def test_non_list_non_str_input_rejected():
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": 42})
    with pytest.raises(ea.RequestError):
        ea.transform_body({"input": {"content": []}})
    with pytest.raises(ea.RequestError):
        ea.transform_body({"model": "m"})  # 缺 input 键


# ---------- HTTP 层:转发、包装与响应契约(§3.2 职责 1/5/6) ----------


def test_multimodal_roundtrip_wraps_and_returns_single_vector(adapter, upstream):
    status, payload = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 200
    assert len(payload["data"]) == 1 and len(payload["data"][0]["embedding"]) == 768
    sent = json.loads(upstream.requests[-1][3])
    assert sent["input"] == [{"content": mm_body()["input"]}]


def test_text_passes_through_without_rewrap(adapter, upstream):
    body = {"model": "m", "input": ["a", "b"]}
    status, _ = http_post(adapter.base_url, "/v1/embeddings", body)
    assert status == 200
    sent = json.loads(upstream.requests[-1][3])
    assert sent == body


def test_multimodal_two_vectors_502(adapter, upstream):
    upstream.response_body = {"data": [
        {"embedding": [0.1] * 768, "index": 0},
        {"embedding": [0.2] * 768, "index": 1},
    ]}
    status, payload = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502 and "error" in payload


def test_multimodal_zero_vectors_502(adapter, upstream):
    upstream.response_body = {"data": []}
    status, _ = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502


def test_multimodal_null_json_502(adapter, upstream):
    upstream.response_body = None  # 上游返回字面 null:合法 JSON 但非对象
    status, _ = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502


def test_multimodal_array_json_502(adapter, upstream):
    upstream.response_body = [1, 2, 3]  # 合法 JSON 数组,无 data 字段
    status, _ = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502


def test_upstream_http_500_maps_to_generic_502(adapter, upstream):
    upstream.response_status = 500
    status, payload = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502
    assert "secret-upstream-detail" not in json.dumps(payload)


def test_upstream_timeout_maps_502():
    fake = FakeUpstream()
    fake.hang_seconds = 3.0
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=0.5, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_post(base, "/v1/embeddings", mm_body())
        assert status == 502
    finally:
        server.shutdown(); server.server_close(); fake.close()


def test_upstream_connection_error_maps_502():
    # 绑定 127.0.0.1:1(必然连接失败)作为上游
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url="http://127.0.0.1:1/v1/embeddings",
        upstream_timeout_seconds=2, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_post(base, "/v1/embeddings", {"model": "m", "input": "x"})
        assert status == 502
    finally:
        server.shutdown(); server.server_close()


def test_oversized_body_413(adapter):
    big = {"model": "m", "input": "x" * 8192}
    status, _ = http_post(adapter.base_url, "/v1/embeddings", big)
    assert status == 413


def test_invalid_json_400(adapter):
    status, _ = http_post(
        adapter.base_url, "/v1/embeddings", None, raw_body=b"{not-json"
    )
    assert status == 400


def test_non_object_json_body_400(adapter):
    # 合法 JSON 但非对象(数组/数字/null)→ 400,不是 5xx
    for raw in (b"[1,2]", b"42", b"null"):
        status, _ = http_post(
            adapter.base_url, "/v1/embeddings", None, raw_body=raw
        )
        assert status == 400


def test_missing_content_length_411(adapter):
    import http.client

    conn = http.client.HTTPConnection(
        "127.0.0.1", adapter.server_address[1], timeout=10
    )
    conn.putrequest("POST", "/v1/embeddings")
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()  # 不发送 Content-Length
    resp = conn.getresponse()
    assert resp.status == 411
    conn.close()


def test_get_embeddings_405(adapter):
    status, _ = http_get(adapter.base_url, "/v1/embeddings")
    assert status == 405


def test_unknown_path_404(adapter):
    status, _ = http_post(adapter.base_url, "/v1/other", {"input": "x"})
    assert status == 404


def test_health_ok_without_upstream():
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url="http://127.0.0.1:1/v1/embeddings",
        upstream_timeout_seconds=2, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = http_get(base, "/health")
        assert status == 200
    finally:
        server.shutdown(); server.server_close()


def test_ready_200_when_upstream_healthy(adapter):
    status, _ = http_get(adapter.base_url, "/ready")
    assert status == 200


def test_ready_503_when_upstream_down():
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url="http://127.0.0.1:1/v1/embeddings",
        upstream_timeout_seconds=2, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_get(base, "/ready")
        assert status == 503
    finally:
        server.shutdown(); server.server_close()


def test_forward_concurrency_capped_at_two():
    fake = FakeUpstream()
    fake.hang_seconds = 1.5
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=10, max_body_bytes=4096, max_concurrent=2,
        forward_wait_seconds=5,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    results = []

    def worker():
        results.append(http_post(base, "/v1/embeddings", {"model": "m", "input": "x"}))

    try:
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert fake.max_inflight_seen <= 2
        assert all(status == 200 for status, _ in results)
    finally:
        server.shutdown(); server.server_close(); fake.close()


def test_overload_beyond_wait_rejected():
    fake = FakeUpstream()
    fake.hang_seconds = 2.0
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=10, max_body_bytes=4096, max_concurrent=1,
        forward_wait_seconds=0.2,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    results = []

    def worker():
        results.append(
            http_post(base, "/v1/embeddings", {"model": "m", "input": "x"})
        )

    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(s for s, _ in results)
        assert statuses == [200, 503]  # 一个转发,一个因等待超时被拒
    finally:
        server.shutdown(); server.server_close(); fake.close()


# ---------- 安全:日志与转发不携带正文/凭据(§3.2 职责 7/8) ----------


def test_logs_and_upstream_contain_no_body_or_credentials(adapter, upstream):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    ea.LOG.addHandler(handler)
    try:
        marker = "TOPSECRET-marker-text-123"
        http_post(
            adapter.base_url, "/v1/embeddings",
            {"model": "m", "input": marker},
            headers={"Authorization": "Bearer TOPSECRET-bearer"},
        )
    finally:
        ea.LOG.removeHandler(handler)
    assert any("200" in m for m in records)  # 有访问日志
    assert not any("TOPSECRET" in m for m in records)  # 但不含正文/凭据
    sent_headers = upstream.requests[-1][2]
    assert "Authorization" not in sent_headers  # 不向本地上游转发凭据头


def test_response_vector_not_logged(adapter, upstream):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    ea.LOG.addHandler(handler)
    try:
        http_post(adapter.base_url, "/v1/embeddings", mm_body())
    finally:
        ea.LOG.removeHandler(handler)
    assert not any("0.1" in m for m in records)  # 向量数值不进日志
