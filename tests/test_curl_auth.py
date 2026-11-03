"""curl 认证 helper 与双向私有隔离脚本的离线行为测试。"""
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import _http_fakes

REPO = Path(__file__).resolve().parents[1]
USER_A_KEY = 'fake-"user-a\\key'
USER_B_KEY = "fake-user-b-key"
ROOT_KEY = "fake-root-must-not-be-loaded"
ADMIN_KEY = "fake-admin-must-not-be-loaded"


def test_code_auth_uses_stdin_and_never_exposes_key_or_body(tmp_path):
    """生产缺陷:把 key 放进 argv、错误转义或打印管理响应体时此测试失败。"""
    capture = tmp_path / "capture"
    capture.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
capture = Path(os.environ['CURL_CAPTURE_DIR'])
capture.joinpath('argv.json').write_text(json.dumps(sys.argv[1:]))
capture.joinpath('stdin.txt').write_text(sys.stdin.read())
args = sys.argv[1:]
if '-o' in args:
    Path(args[args.index('-o') + 1]).write_text('sensitive-response-body')
if '-w' in args:
    fmt = args[args.index('-w') + 1]
    sys.stdout.write(fmt.replace('%{http_code}', '200'))
""")
    fake_curl.chmod(0o755)
    script = """
source "$1"
code_auth "X-API-Key: $FAKE_USER_KEY" --get 'http://127.0.0.1:1933/api/v1/fs/ls?uri=viking://resources'
"""
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CURL_CAPTURE_DIR": str(capture),
        "FAKE_USER_KEY": USER_A_KEY,
        "ROOT_KEY_FILE": str(tmp_path / "missing-root"),
        "ADMIN_KEY_FILE": str(tmp_path / "missing-admin"),
        "ROOT_KEY": ROOT_KEY,
        "ADMIN_KEY": ADMIN_KEY,
    })
    proc = subprocess.run(
        ["bash", "-c", script, "bash", str(REPO / "tests" / "curl_auth.sh")],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "200"
    argv = json.loads((capture / "argv.json").read_text())
    assert argv[0] == "-q"
    assert ["-K", "-"] == argv[argv.index("-K"):argv.index("-K") + 2]
    assert USER_A_KEY not in json.dumps(argv)
    assert ROOT_KEY not in json.dumps(argv) and ADMIN_KEY not in json.dumps(argv)
    assert (capture / "stdin.txt").read_text() == (
        'header = "X-API-Key: fake-\\"user-a\\\\key"\n'
    )
    combined = proc.stdout + proc.stderr + json.dumps(argv)
    assert "sensitive-response-body" not in combined
    assert ROOT_KEY not in combined and ADMIN_KEY not in combined


class FakeIdentityServer(_http_fakes.FakeServer):
    """只实现隔离脚本使用的 write/read/delete，跟踪私有身份与清理。"""

    def __init__(self, allow_cross=False):
        self.allow_cross = allow_cross
        self.resources = {}
        self.writes = []
        self.deletes = []
        self.rejections = []
        server = self

        class Handler(_http_fakes.FakeBaseHandler):
            def _identity(self):
                auth = self.headers.get("Authorization")
                api_key = self.headers.get("X-API-Key")
                if auth == f"Bearer {USER_A_KEY}" or api_key == USER_A_KEY:
                    return "alpha"
                if auth == f"Bearer {USER_B_KEY}" or api_key == USER_B_KEY:
                    return "beta"
                return None

            def _body(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length) or b"{}")

            def do_POST(self):
                if self.path != "/api/v1/content/write":
                    self._json(404, {"status": "error"})
                    return
                uid = self._identity()
                body = self._body()
                uri = body.get("uri", "")
                expected = f"viking://user/{uid}/resources/identity-isolation-"
                if uid is None or not uri.startswith(expected):
                    server.rejections.append({
                        "identity_present": uid is not None, "uri": uri,
                        "expected_prefix": expected,
                        "received_auth": self.headers.get("Authorization"),
                    })
                    self._json(403, {"status": "error"})
                    return
                if body.get("mode") != "replace" or body.get("wait") is not True \
                        or body.get("processing_mode") != "vectors_only":
                    self._json(400, {"status": "error"})
                    return
                server.resources[uri] = uid
                server.writes.append((uid, uri))
                self._json(200, {"status": "ok", "result": {"uri": uri}})

            def do_GET(self):
                parsed = urlparse(self.path)
                uid = self._identity()
                uri = parse_qs(parsed.query).get("uri", [""])[0]
                if parsed.path != "/api/v1/content/read" or uid is None:
                    self._json(404, {"status": "error"})
                    return
                owner = server.resources.get(uri)
                if owner is None:
                    self._json(404, {"status": "error"})
                    return
                if owner != uid and not server.allow_cross:
                    # 两个方向分别覆盖 403/404 的不可见语义。
                    self._json(403 if uid == "alpha" else 404, {"status": "error"})
                    return
                self._json(200, {"status": "ok", "result": "fixture"})

            def do_DELETE(self):
                parsed = urlparse(self.path)
                uid = self._identity()
                query = parse_qs(parsed.query)
                uri = query.get("uri", [""])[0]
                if parsed.path != "/api/v1/fs" or uid is None:
                    self._json(403, {"status": "error"})
                    return
                owner = server.resources.get(uri)
                if owner is not None and owner != uid:
                    self._json(404, {"status": "error"})
                    return
                server.resources.pop(uri, None)
                server.deletes.append((uid, uri))
                self._json(200, {"status": "ok", "result": {"uri": uri}})

        super().__init__(Handler)


@pytest.fixture()
def identity_server():
    fake = FakeIdentityServer()
    yield fake
    fake.close()


def run_isolation(server, tmp_path, stream_a=False):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    key_a = secrets / "alpha"
    key_b = secrets / "beta"
    key_a.write_text(USER_A_KEY)
    key_b.write_text(USER_B_KEY)
    env = dict(os.environ)
    env.update({
        "USER_A": "alpha", "USER_B": "beta",
        "USER_A_KEY_FILE": str(key_a), "USER_B_KEY_FILE": str(key_b),
        "BASE": server.url, "FAKE_USER_KEY": USER_A_KEY,
    })
    if stream_a:
        return subprocess.run(
            ["bash", "-c", 'USER_A_KEY_FILE=<(printf "%s" "$FAKE_USER_KEY") bash "$1"',
             "bash", str(REPO / "tests" / "e2e_user_isolation.sh")],
            env=env, capture_output=True, text=True, check=False,
        )
    return subprocess.run(
        ["bash", str(REPO / "tests" / "e2e_user_isolation.sh")],
        env=env, capture_output=True, text=True, check=False,
    )


def test_isolation_script_enforces_cross_denial_and_cleans(identity_server, tmp_path):
    """生产缺陷:交叉读取未判失败或未删除夹具时，真实 HTTP 行为会失败。"""
    proc = run_isolation(identity_server, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr + repr(identity_server.rejections)
    assert len(identity_server.writes) == 2
    assert len(identity_server.deletes) == 2
    assert identity_server.resources == {}
    combined = proc.stdout + proc.stderr
    assert USER_A_KEY not in combined and USER_B_KEY not in combined
    assert "PASS" in combined


def test_isolation_accepts_process_substitution_key(identity_server, tmp_path):
    """生产缺陷:用 -s 拒绝 /dev/fd 会迫使 meteor 额外落一份裸 key。"""
    proc = run_isolation(identity_server, tmp_path, stream_a=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert identity_server.resources == {}


def test_isolation_script_cleans_when_cross_read_was_allowed(tmp_path):
    """生产缺陷:隔离断言失败时跳过 trap 清理会留下真实测试残留。"""
    server = FakeIdentityServer(allow_cross=True)
    try:
        proc = run_isolation(server, tmp_path)
        assert proc.returncode != 0
        assert server.resources == {}
        assert len(server.deletes) == 2
    finally:
        server.close()
