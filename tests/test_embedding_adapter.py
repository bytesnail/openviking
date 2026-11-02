"""embedding_adapter.py 单元测试(设计 §9.1.3)。

覆盖:纯文本/文本批处理不变;OpenViking 多模态 parts 只加一层 content 包装;
已包装输入不重复包装;非法 part、混合输入、超大输入被拒绝;多模态多向量/空向量
响应返回 502;上游状态与错误安全处理,日志不含正文。
"""
import http.client
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

import _http_fakes
import embedding_adapter as ea

# keep-alive 关闭/重连的合格结果二选一:连接级失败,或客户端自动重连后的
# 正常应答(2026-11-03 检视:此元组原先在四个用例里逐字重复)。
CLOSED_OR_RECONNECT = (
    http.client.RemoteDisconnected, ConnectionError, http.client.BadStatusLine,
    OSError,
)


# ---------- 测试工具:可编程的假上游(骨架见 tests/_http_fakes.py) ----------


class FakeUpstream(_http_fakes.FakeServer):
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

        class Handler(_http_fakes.FakeBaseHandler):
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
                        self._json(200, {"status": "ok"})
                        return
                    status = upstream.response_status
                    if status == 200:
                        payload = upstream.response_body
                    else:
                        payload = {"error": "secret-upstream-detail"}
                    self._json(status, payload)
                finally:
                    with upstream._lock:
                        upstream._inflight -= 1

            do_GET = _handle
            do_POST = _handle

        super().__init__(Handler)

    @property
    def url(self):
        return super().url + "/v1/embeddings"


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


# 测试客户端与生产同纪律:直连(禁代理)opener,防 http_proxy 环境把 loopback
# 测试路由到代理造成大面积假失败,也让安全用例的 Authorization 头不再过代理
# (2026-11-03 检视;原先用继承代理的裸 urllib.urlopen)。


def http_post(base, path, obj, headers=None, raw_body=None):
    data = raw_body if raw_body is not None else json.dumps(obj).encode()
    req = urllib.request.Request(
        base + path, data=data, method="POST", headers=headers or {}
    )
    req.add_header("Content-Type", "application/json")
    try:
        with ea._DIRECT_OPENER.open(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def http_get(base, path):
    try:
        with ea._DIRECT_OPENER.open(base + path, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def base_url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


@contextmanager
def standalone_server(**overrides):
    """make_server(port=0) + 线程起服 + 确定性收尾;替代原先 8 处手写样板
    (2026-11-03 检视)。FakeUpstream 由调用方创建/关闭(需要编程其状态)。"""
    kwargs = {
        "host": "127.0.0.1", "port": 0,
        "upstream_url": "http://127.0.0.1:1/v1/embeddings",
        "upstream_timeout_seconds": 2, "max_body_bytes": 4096,
        "max_concurrent": 2,
    }
    kwargs.update(overrides)
    server = ea.make_server(**kwargs)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


class Capture(logging.Handler):
    """收集日志消息;两处安全用例共用(2026-11-03 检视前定义了两次)。"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


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
    out, needs_check = ea.transform_body(body)
    assert out is body and needs_check is False


def test_text_batch_forwarded_unchanged():
    body = {"model": "m", "input": ["a", "b"]}
    out, needs_check = ea.transform_body(body)
    assert out is body and needs_check is False


def test_openviking_parts_get_content_wrapper():
    body = mm_body()
    out, needs_check = ea.transform_body(body)
    assert needs_check is True
    assert out["input"] == [{"content": body["input"]}]
    assert out["model"] == "embeddinggemma-2"  # 其余字段保留
    assert out["encoding_format"] == "float"


def test_already_wrapped_input_not_double_wrapped():
    body = {"model": "m", "input": [{"content": [dict(TEXT_PART), dict(IMAGE_PART)]}]}
    out, needs_check = ea.transform_body(body)
    # 单项已包装输入不加壳(原样转发),但仍属"恰好一个向量"契约,需校验
    # (2026-11-03 起 transform_body 直接返回该语义,调用点不再二次推导)。
    assert out is body and needs_check is True


def test_image_only_parts_get_content_wrapper():
    """纯图(无 text part)也属"非空且全为受支持 parts",须包装(2026-10-26 补测缺口)。"""
    parts = [dict(IMAGE_PART)]
    out, needs_check = ea.transform_body({"model": "m", "input": parts})
    assert needs_check is True
    assert out["input"] == [{"content": parts}]


def test_text_only_parts_list_wrapped():
    """全 text part 的列表同样包装,不当作文本批处理。"""
    parts = [dict(TEXT_PART), dict(TEXT_PART)]
    out, needs_check = ea.transform_body({"model": "m", "input": parts})
    assert needs_check is True
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


def test_prewrapped_batch_over_one_item_rejected_400(adapter, upstream):
    """多项 wrapped 批量输入必产生多向量(必然 502),应在转换阶段 400 拒绝,
    不浪费整批上游推理(2026-11-02 检视)。"""
    body = {"model": "m", "input": [
        {"content": [dict(TEXT_PART)]},
        {"content": [dict(TEXT_PART)]},
    ]}
    status, _ = http_post(adapter.base_url, "/v1/embeddings", body)
    assert status == 400
    assert upstream.requests == []


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
    try:
        with standalone_server(
            upstream_url=fake.url, upstream_timeout_seconds=0.5
        ) as server:
            status, _ = http_post(base_url(server), "/v1/embeddings", mm_body())
            assert status == 502
    finally:
        fake.close()


def test_upstream_connection_error_maps_502():
    # 绑定 127.0.0.1:1(必然连接失败)作为上游
    with standalone_server() as server:
        status, _ = http_post(
            base_url(server), "/v1/embeddings", {"model": "m", "input": "x"}
        )
        assert status == 502


def test_upstream_response_over_cap_maps_502():
    fake = FakeUpstream()
    try:
        with standalone_server(
            upstream_url=fake.url, max_concurrent=1, max_response_body_bytes=128
        ) as server:
            status, _ = http_post(base_url(server), "/v1/embeddings", mm_body())
            assert status == 502
    finally:
        fake.close()


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
        except CLOSED_OR_RECONNECT:
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
    """非法 Content-Length 的 400 之后连接必须关闭(体未读,不能复用)。

    服务端现以 `Connection: close` 显式声明关闭,http.client 会透明重连:
    合格结果二选一——连接级失败,或重连后得到完整、不含残留体字节污染的
    正常应答;绝不允许出现由未读体字节错位解析出的伪应答。
    """
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
        assert resp.getheader("Connection") == "close"
        resp.read()
        outcome = "error"
        try:
            conn.putrequest("POST", "/v1/embeddings")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "2")
            conn.endheaders()
            conn.send(b"{}")
            outcome = conn.getresponse()
        except CLOSED_OR_RECONNECT:
            pass
        if outcome == "error":
            pass  # 连接级失败:同样正确(客户端必须重连)
        else:
            raw = outcome.read()
            assert outcome.status == 400
            # `{}` 是合法 JSON 但非对象 → transform 阶段拒绝
            assert json.loads(raw)["error"]["type"] == "invalid_request"
    finally:
        conn.close()


def test_error_response_declares_connection_close(adapter):
    """强制关闭的错误响应必须显式带 Connection: close,协议正确的
    keep-alive 客户端才不会复用注定被关的连接(2026-11-02 检视)。"""
    import http.client

    conn = http.client.HTTPConnection(
        "127.0.0.1", adapter.server_address[1], timeout=10
    )
    try:
        raw = b'{"input":"x"}x'  # 14 字节,合法长度但非法 JSON → 400
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders()
        conn.send(raw)
        resp = conn.getresponse()
        assert resp.status == 400
        assert resp.getheader("Connection") == "close"
        resp.read()
    finally:
        conn.close()


@pytest.mark.parametrize("cl", [b"+13", b"1_3"])
def test_non_canonical_content_length_rejected(adapter, cl):
    """Content-Length 只允许纯 ASCII 数字:int() 还会接受 "+" 前缀与
    下划线分组(均等于 13),须按 RFC 9110 收紧(2026-11-03 检视)。
    附带发送完整合法正文:旧行为会按解析出的 13 字节放行成 200。"""
    sock = socket.create_connection(
        ("127.0.0.1", adapter.server_address[1]), timeout=10
    )
    try:
        body = b'{"input":"x"} '  # 13 字节合法 JSON
        sock.sendall(
            b"POST /v1/embeddings HTTP/1.1\r\nHost: t\r\n"
            b"Content-Type: application/json\r\nContent-Length: "
            + cl + b"\r\n\r\n" + body
        )
        data = sock.recv(4096)
        assert data.startswith(b"HTTP/1.1 400")
    finally:
        sock.close()


def test_early_reject_response_survives_unread_body(adapter):
    """拒绝路径未读正文即关连接:有界排水后,客户端把声明正文全量发出
    (暂不读响应)也应收到 413,而不是被内核 RST 吞掉已写出的响应
    (2026-11-03 检视)。"""
    conn = http.client.HTTPConnection(
        "127.0.0.1", adapter.server_address[1], timeout=10
    )
    try:
        body = b"x" * 8192  # adapter fixture 的 max_body_bytes=4096
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        resp = conn.getresponse()
        assert resp.status == 413
        resp.read()
    finally:
        conn.close()


def test_write_failure_after_response_started_writes_no_second_status(monkeypatch):
    """200 已开始写出后写失败:兜底路径绝不能再写第二个状态行,否则客户端
    收到 200 头+截断正文+内嵌 500 块的损坏流(2026-11-03 检视)。"""
    fake = FakeUpstream()
    # ~5MB 有效单向量:超过收发两侧内核缓冲,客户端不读时服务端写超时,
    # 恰好触发"响应已开始"的兜底路径。
    fake.response_body = {"data": [{"embedding": [0.1] * 600000, "index": 0}]}
    try:
        with standalone_server(
            upstream_url=fake.url, upstream_timeout_seconds=10
        ) as server:
            monkeypatch.setattr(ea.AdapterHandler, "timeout", 0.3)
            body = json.dumps(mm_body()).encode()
            sock = socket.create_connection(
                ("127.0.0.1", server.server_address[1]), timeout=5
            )
            try:
                sock.sendall(
                    b"POST /v1/embeddings HTTP/1.1\r\nHost: t\r\n"
                    b"Content-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                time.sleep(0.8)  # > 0.3s 写超时,兜底路径已执行完毕
                sock.settimeout(2)
                data = b""
                try:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    pass
                assert data.startswith(b"HTTP/1.1 200")
                assert data.count(b"HTTP/1.1") == 1  # 旧 bug 会追加第二个状态行
            finally:
                sock.close()
    finally:
        fake.close()


def test_health_ok_without_upstream():
    with standalone_server() as server:
        status, body = http_get(base_url(server), "/health")
        assert status == 200


def test_ready_200_when_upstream_healthy(adapter):
    status, _ = http_get(adapter.base_url, "/ready")
    assert status == 200


def test_ready_503_when_upstream_down():
    with standalone_server() as server:
        status, _ = http_get(base_url(server), "/ready")
        assert status == 503


def test_connection_handler_threads_are_bounded():
    with standalone_server(
        upstream_timeout_seconds=1, max_concurrent=1, max_connections=2
    ) as server:
        baseline = threading.active_count()
        clients = []
        try:
            for _ in range(4):
                client = socket.create_connection(
                    ("127.0.0.1", server.server_address[1])
                )
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


def test_forward_concurrency_capped_at_two():
    fake = FakeUpstream()
    fake.hang_seconds = 1.5
    try:
        with standalone_server(
            upstream_url=fake.url, upstream_timeout_seconds=10,
            forward_wait_seconds=5,
        ) as server:
            results = []
            start = threading.Barrier(3)

            def worker():
                start.wait()
                results.append(
                    http_post(
                        base_url(server), "/v1/embeddings",
                        {"model": "m", "input": "x"},
                    )
                )

            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert fake.max_inflight_seen <= 2
            assert all(status == 200 for status, _ in results)
    finally:
        fake.close()


def test_overload_beyond_wait_rejected():
    fake = FakeUpstream()
    fake.hang_seconds = 2.0
    try:
        with standalone_server(
            upstream_url=fake.url, upstream_timeout_seconds=10,
            max_concurrent=1, forward_wait_seconds=0.2,
        ) as server:
            results = []
            # 两请求必须同时在位(Barrier):否则第二个晚到时第一个已释放槽,
            # 双双 200 的假阴会让正确代码误报失败(2026-11-03 检视)。
            start = threading.Barrier(2)

            def worker():
                start.wait()
                results.append(
                    http_post(
                        base_url(server), "/v1/embeddings",
                        {"model": "m", "input": "x"},
                    )
                )

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            statuses = sorted(s for s, _ in results)
            assert statuses == [200, 503]  # 一个转发,一个因等待超时被拒
    finally:
        fake.close()


# ---------- 安全:日志与转发不携带正文/凭据(§3.2 职责 7/8) ----------


def test_logs_and_upstream_contain_no_body_or_credentials(adapter, upstream):
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
    assert any("200" in m for m in handler.records)  # 有访问日志
    assert not any("TOPSECRET" in m for m in handler.records)  # 但不含正文/凭据
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
    handler = Capture()
    ea.LOG.addHandler(handler)
    try:
        http_post(adapter.base_url, "/v1/embeddings", mm_body())
    finally:
        ea.LOG.removeHandler(handler)
    assert not any("0.1" in m for m in handler.records)  # 向量数值不进日志


def test_query_string_not_logged(adapter, upstream):
    """查询串属于请求内容(可携带凭据),访问日志只保留去查询串的路径
    (2026-11-03 检视:原先 self.path 原样入日志)。"""
    handler = Capture()
    ea.LOG.addHandler(handler)
    try:
        http_post(
            adapter.base_url, "/v1/embeddings?token=TOPSECRET-query",
            {"input": "x"},
        )
    finally:
        ea.LOG.removeHandler(handler)
    joined = "\n".join(handler.records)
    assert "TOPSECRET-query" not in joined
    assert "/v1/embeddings" in joined


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
    """GET /health 带未读体后必须关闭连接并显式声明;复用方重连后得到的
    必须是干净的新应答,绝不能是体字节错位解析出的伪应答。"""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("GET", "/health")
        conn.putheader("Content-Length", "2")
        conn.endheaders()
        conn.send(b"{}")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Connection") == "close"
        resp.read()
        outcome = "error"
        try:
            conn.putrequest("GET", "/health")
            conn.endheaders()
            outcome = conn.getresponse()
        except CLOSED_OR_RECONNECT:
            pass
        if outcome == "error":
            pass  # 连接级失败:正确契约
        else:
            raw = outcome.read()
            assert outcome.status == 200
            assert json.loads(raw) == {"status": "ok"}
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
        assert resp.getheader("Connection") == "close"
        assert json.loads(resp.read())["error"]["type"] == "request_timeout"
        outcome = "error"
        try:
            conn.putrequest("POST", "/v1/embeddings")
            conn.putheader("Content-Length", "0")
            conn.endheaders()
            outcome = conn.getresponse()
        except CLOSED_OR_RECONNECT:
            pass
        if outcome == "error":
            pass  # 连接级失败:正确契约(服务端已关,复用必须失败或重连)
        else:
            raw = outcome.read()
            assert outcome.status == 400
            assert json.loads(raw)["error"]["type"] == "invalid_json"
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

    monkeypatch.setattr(ea.AdapterHandler, "timeout", 2.0)
    body = json.dumps({"model": "m", "input": "x"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", "/v1/embeddings")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        # 逐字节慢发约 0.6s(deadline 2.0s 的 30%):占去约 0.6s、剩余 ~1.4s。
        # 2026-11-03 检视:旧版 0.3s deadline + 0.25s sleep 仅留 ~50ms 余量,
        # 调度抖动即可让正确实现 flake;放大时间轴后两侧余量均为 0.3s。
        for byte in body:
            conn.send(bytes([byte]))
            time.sleep(0.6 / len(body))
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()

        # 睡 1.7s:> 未恢复 bug 的剩余 ~1.4s(触发回归断言),
        # < 恢复后的 2.0s(通过侧余量 0.3s)。
        time.sleep(1.7)  # > 未恢复时的剩余 timeout(~0.2s),< 恢复后的 0.3s
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
    finally:
        conn.close()
