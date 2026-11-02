"""测试共用:可编程假 HTTP server 的公共骨架。

test_bootstrap_auth.FakeOvServer 与 test_embedding_adapter.FakeUpstream 共享
的服务器生命周期样板(127.0.0.1:0 起服、daemon 线程 poll_interval=0.01、
url/close 收尾)与 JSON 应答/日志静音的 handler 基类;生命周期修复只落这
一处(2026-11-03 检视:原先两份 ~40 行拷贝各自演化)。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeBaseHandler(BaseHTTPRequestHandler):
    """JSON 应答与日志静音的公共 handler 基类。"""

    def _json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端已断开:假服务器不再向已关闭 socket 写响应

    def log_message(self, *args):
        pass  # 静音假服务器日志


class FakeServer:
    """一次性假服务器:构造即起服,close() 确定性收尾。"""

    def __init__(self, handler_class):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
