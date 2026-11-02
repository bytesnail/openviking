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
                with upstream._lock:
                    upstream._inflight += 1
                    upstream.max_inflight_seen = max(
                        upstream.max_inflight_seen, upstream._inflight
                    )
                try:
                    if upstream.hang_seconds:
                        time.sleep(upstream.hang_seconds)
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
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # 适配器超时/断开后,假上游不再向已关闭 socket 写响应
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
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def test_image_only_parts_get_content_wrapper():
    """纯图(无 text part)也属"非空且全为受支持 parts",须包装(2026-10-26 补测缺口)。"""
    parts = [dict(IMAGE_PART)]
    out, wrapped = ea.transform_body({"model": "m", "input": parts})
    assert wrapped is True
    assert out["input"] == [{"content": parts}]


def test_text_only_parts_list_wrapped():
    """全 text part 的列表同样包装,不当作文本批处理。"""
    parts = [dict(TEXT_PART), dict(TEXT_PART)]
    out, wrapped = ea.transform_body({"model": "m", "input": parts})
    assert wrapped is True
    assert out["input"] == [{"content": parts}]


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


@pytest.mark.parametrize("response_body", [
    {"data": [{}]},
    {"data": [{"embedding": None}]},
    {"data": [{"embedding": []}]},
    {"data": [{"embedding": [0.1, "bad"]}]},
    {"data": [{"embedding": [float("nan")]}]},
    {"data": [{"embedding": [0.0] * 768}]},  # 全零向量:余弦未定义
])
def test_multimodal_invalid_vector_502(adapter, upstream, response_body):
    upstream.response_body = response_body
    status, _ = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502


def test_prewrapped_multimodal_invalid_vector_502(adapter, upstream):
    upstream.response_body = {"data": []}
    body = {"model": "m", "input": [{"content": [dict(TEXT_PART), dict(IMAGE_PART)]}]}
    status, _ = http_post(adapter.base_url, "/v1/embeddings", body)
    assert status == 502


def test_upstream_http_500_maps_to_generic_502(adapter, upstream):
    upstream.response_status = 500
    status, payload = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 502
    assert "secret-upstream-detail" not in json.dumps(payload)


def test_upstream_permanent_400_preserves_client_error(adapter, upstream):
    upstream.response_status = 400
    status, payload = http_post(adapter.base_url, "/v1/embeddings", mm_body())
    assert status == 400
    assert "secret-upstream-detail" not in json.dumps(payload)


def test_upstream_timeout_maps_502():
    fake = FakeUpstream()
    fake.hang_seconds = 3.0
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=0.5, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
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
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_post(base, "/v1/embeddings", {"model": "m", "input": "x"})
        assert status == 502
    finally:
        server.shutdown(); server.server_close()


def test_upstream_response_over_cap_maps_502():
    fake = FakeUpstream()
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=2, max_body_bytes=4096, max_concurrent=1,
        max_response_body_bytes=128,
    )
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_post(base, "/v1/embeddings", mm_body())
        assert status == 502
    finally:
        server.shutdown(); server.server_close(); fake.close()


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


def test_duplicate_content_length_rejected(adapter, upstream):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "13")
        conn.putheader("Content-Length", "100")
        conn.endheaders()
        conn.send(b'{"input":"x"}')
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        conn.close()
    assert upstream.requests == []


def test_transfer_encoding_with_length_rejected(adapter, upstream):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.putheader("Content-Length", "13")
        conn.endheaders()
        conn.send(b'{"input":"x"}')
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        conn.close()
    assert upstream.requests == []


def test_get_embeddings_405(adapter):
    status, _ = http_get(adapter.base_url, "/v1/embeddings")
    assert status == 405


def test_unknown_path_404(adapter):
    status, _ = http_post(adapter.base_url, "/v1/other", {"input": "x"})
    assert status == 404


def test_unknown_path_then_reuse_same_connection(adapter):
    """404(带请求体)之后复用同一连接:绝不能得到由残留体字节解析出的
    伪应答(旧 bug:不读体不关连接 → 复用方收到 HTML 400 垃圾页)。
    合格结果二选一:连接级失败(服务端已关),或客户端自动重连后的
    正常 200 JSON;HTML 垃圾页一律判负。"""
    import http.client

    conn = http.client.HTTPConnection(
        "127.0.0.1", adapter.server_address[1], timeout=10
    )
    try:
        conn.request(
            "POST", "/v1/other", body=json.dumps({"input": "x"}),
            headers={"Content-Type": "application/json"},
        )
        resp1 = conn.getresponse()
        assert resp1.status == 404
        resp1.read()

        outcome = "error"
        try:
            conn.request(
                "POST", "/v1/embeddings", body=json.dumps({"model": "m", "input": "x"}),
                headers={"Content-Type": "application/json"},
            )
            resp2 = conn.getresponse()
            outcome = (resp2.status, resp2.read())
        except (http.client.RemoteDisconnected, ConnectionError,
                http.client.BadStatusLine, OSError):
            outcome = "connection-closed"
        if outcome == "connection-closed":
            pass  # 服务端关闭连接:正确契约,复用必须由客户端重连
        else:
            status, raw2 = outcome
            payload2 = json.loads(raw2)  # 旧 bug 的 HTML 垃圾页在此抛 ValueError
            assert status == 200 and "data" in payload2
    finally:
        conn.close()


def test_invalid_content_length_closes_connection(adapter):
    """非法 Content-Length 的 400 之后连接必须关闭(体未读,不能复用)。"""
    import http.client

    conn = http.client.HTTPConnection(
        "127.0.0.1", adapter.server_address[1], timeout=10
    )
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        conn.send(b'{"input":"x"}')
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
        # 服务端已关连接:复用必须失败(写入即 EPIPE 或读取得到连接级错误),
        # 而不是收到伪应答
        with pytest.raises((http.client.RemoteDisconnected, ConnectionError,
                            http.client.BadStatusLine, OSError)):
            conn.putrequest("POST", "/v1/embeddings")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "2")
            conn.endheaders()
            conn.send(b"{}")
            conn.getresponse()
    finally:
        conn.close()


def test_health_ok_without_upstream():
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url="http://127.0.0.1:1/v1/embeddings",
        upstream_timeout_seconds=2, max_body_bytes=4096, max_concurrent=2,
    )
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
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
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _ = http_get(base, "/ready")
        assert status == 503
    finally:
        server.shutdown(); server.server_close()


def test_connection_handler_threads_are_bounded():
    import socket

    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url="http://127.0.0.1:1/v1/embeddings",
        upstream_timeout_seconds=1, max_body_bytes=4096, max_concurrent=1,
        max_connections=2,
    )
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    baseline = threading.active_count()
    clients = []
    try:
        for _ in range(4):
            client = socket.create_connection(("127.0.0.1", server.server_address[1]))
            # 不发送任何字节,handler 阻塞在 request-line read;关闭时不会产生
            # 半请求解析/响应 BrokenPipe 噪音。
            clients.append(client)
        time.sleep(0.15)
        # accept 主线程在 connection_slots 上等待,第三/第四连接只留在内核
        # backlog,不能继续派生 handler 线程。
        assert threading.active_count() - baseline <= 2
    finally:
        for client in clients:
            client.close()
        server.shutdown(); server.server_close()


def test_forward_concurrency_capped_at_two():
    fake = FakeUpstream()
    fake.hang_seconds = 1.5
    server = ea.make_server(
        host="127.0.0.1", port=0, upstream_url=fake.url,
        upstream_timeout_seconds=10, max_body_bytes=4096, max_concurrent=2,
        forward_wait_seconds=5,
    )
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    results = []
    start = threading.Barrier(3)

    def worker():
        start.wait()
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
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
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


def test_upstream_log_origin_strips_userinfo_path_and_query():
    logged = ea._upstream_log_origin(
        "http://dummy-user:dummy-password@127.0.0.1:18351/private-path?token=dummy-token"
    )
    assert logged == "http://127.0.0.1:18351"
    for sensitive in ("dummy-user", "dummy-password", "private-path", "dummy-token"):
        assert sensitive not in logged


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


def test_short_declared_body_is_rejected(adapter, upstream):
    import http.client
    import socket

    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "100")
        conn.endheaders()
        body = b'{"input":"x"}'
        conn.send(body)
        conn.sock.shutdown(socket.SHUT_WR)
        resp = conn.getresponse()
        assert resp.status == 400
        assert json.loads(resp.read())["error"]["type"] == "invalid_request"
    finally:
        conn.close()
    assert upstream.requests == []


def test_get_health_with_body_cannot_desync_next_request(adapter):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("GET", "/health")
        conn.putheader("Content-Length", "2")
        conn.endheaders()
        conn.send(b"{}")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        with pytest.raises((http.client.RemoteDisconnected, ConnectionError,
                            http.client.BadStatusLine, OSError)):
            conn.putrequest("GET", "/health")
            conn.endheaders()
            conn.getresponse()
    finally:
        conn.close()


def test_body_read_timeout_is_408_and_closes(adapter, monkeypatch):
    import http.client

    monkeypatch.setattr(ea.AdapterHandler, "timeout", 0.1)
    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "10")
        conn.endheaders()
        conn.send(b"{")
        resp = conn.getresponse()
        assert resp.status == 408
        assert json.loads(resp.read())["error"]["type"] == "request_timeout"
        with pytest.raises((http.client.RemoteDisconnected, ConnectionError,
                            http.client.BadStatusLine, OSError)):
            conn.putrequest("POST", "/v1/embeddings")
            conn.putheader("Content-Length", "0")
            conn.endheaders()
            conn.getresponse()
    finally:
        conn.close()


def test_slow_drip_body_cannot_exceed_total_deadline(adapter, monkeypatch):
    import http.client

    monkeypatch.setattr(ea.AdapterHandler, "timeout", 0.15)
    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    conn.putrequest("POST", "/v1/embeddings")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "10")
    conn.endheaders()

    def drip():
        try:
            for _ in range(10):
                conn.send(b"x")
                time.sleep(0.03)
        except OSError:
            pass

    sender = threading.Thread(target=drip, daemon=True)
    sender.start()
    started = time.monotonic()
    try:
        resp = conn.getresponse()
        assert resp.status == 408
        assert time.monotonic() - started < 1
        resp.read()
    finally:
        conn.close()
        sender.join(timeout=1)


def test_keepalive_restores_header_timeout_after_body_read(adapter, upstream, monkeypatch):
    import http.client

    monkeypatch.setattr(ea.AdapterHandler, "timeout", 0.3)
    body = json.dumps({"model": "m", "input": "x"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        # 首轮占去大部分 deadline,使剩余的 socket timeout 小于下一轮等待。
        for byte in body:
            conn.send(bytes([byte]))
            time.sleep(0.2 / len(body))
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()

        time.sleep(0.2)  # > 首轮剩余 timeout,但 < 恢复后的 0.3s
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
    finally:
        conn.close()
