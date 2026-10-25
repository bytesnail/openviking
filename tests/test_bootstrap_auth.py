"""bootstrap_auth.py 单元测试(设计 §9.1.4)。

全部使用临时沙盒(HOME、CLI 配置、密钥文件)与进程内假 OpenViking HTTP
server;ov CLI 用可执行 stub 替身(真实 CLI 在部署验收时联调)。
覆盖:首次初始化、重跑幂等、账户已存在但丢 key、root/user key 无效、
服务不可达、输出不含任何密钥。
"""
import json
import os
import stat
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import bootstrap_auth as ba

REPO = Path(__file__).resolve().parents[1]
ROOT_KEY = "ROOTKEY-test-0000000000000000000000000000"
USER_KEY = "USERKEY-test-1111111111111111111111111111"


# ---------- 假 OpenViking server ----------


class FakeOvServer:
    """实现 bootstrap_auth 依赖的接口:POST /api/v1/admin/accounts、
    POST /api/v1/admin/accounts/default/users(v0.5.2 首启自动建空壳账户后的
    正规注册路径)、GET /health(带 User key 认证)。"""

    def __init__(self):
        self.shell_account_exists = True   # v0.5.2 服务端首启行为
        self.user_exists = False
        self.accepted_user_key = None
        self.accounts_calls = 0
        self.users_calls = 0
        self.drop_user_key = False  # 模拟 trusted 模式缺 user_key 字段
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _auth_root(self):
                if self.headers.get("Authorization") == f"Bearer {ROOT_KEY}":
                    return True
                self._json(401, {"status": "error", "message": "invalid root key"})
                return False

            def do_POST(self):
                if self.path == "/api/v1/admin/accounts":
                    server.accounts_calls += 1
                    if not self._auth_root():
                        return
                    if server.shell_account_exists:
                        self._json(409, {
                            "status": "error",
                            "error": {"code": "ALREADY_EXISTS",
                                      "message": "Account already exists: default"},
                        })
                        return
                    server.shell_account_exists = True
                    self._register_result(
                        {"account_id": "default", "admin_user_id": "default"}
                    )
                    return
                if self.path == "/api/v1/admin/accounts/default/users":
                    server.users_calls += 1
                    if not self._auth_root():
                        return
                    if server.user_exists:
                        self._json(409, {
                            "status": "error",
                            "error": {"code": "ALREADY_EXISTS",
                                      "message": "User already exists: default"},
                        })
                        return
                    server.user_exists = True
                    self._register_result({"account_id": "default", "user_id": "default"})
                    return
                self._json(404, {"error": "not found"})

            def _register_result(self, result):
                if not server.drop_user_key:
                    result["user_key"] = USER_KEY
                    server.accepted_user_key = USER_KEY
                self._json(200, {"status": "ok", "result": result})

            def do_GET(self):
                if self.path != "/health":
                    self._json(404, {"error": "not found"})
                    return
                auth = self.headers.get("Authorization")
                if auth != f"Bearer {server.accepted_user_key}":
                    self._json(401, {"status": "error"})
                    return
                self._json(200, {"status": "ok", "healthy": True})

            def _json(self, status, obj):
                raw = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------- ov CLI stub:读 env 密钥、写沙盒 ovcli.conf、做真实认证探测 ----------

OV_STUB = f'''#!/usr/bin/env python3
import json, os, sys, urllib.request, urllib.error
cfg_path = os.environ["OPENVIKING_CLI_CONFIG_FILE"]
args = sys.argv[1:]
if args[:1] == ["language"]:
    # 模拟真实 CLI:语言设置写入独立 settings 文件
    settings = os.path.join(os.path.dirname(cfg_path), "ovcli.settings.conf")
    with open(settings, "w") as fh:
        json.dump({{"language": args[1] if len(args) > 1 else "en"}}, fh)
    sys.exit(0)
if args[:3] == ["config", "add", "custom"]:
    if os.environ.get("OV_STUB_CONFIG_FAIL") == "1":
        sys.exit(3)
    url = args[args.index("--url") + 1]
    key = os.environ[os.environ.get("OV_STUB_USER_ENV", "OPENVIKING_USER_API_KEY")]
    conf = {{"active": "local", "profiles": {{"local": {{"url": url, "api_key": key}}}}}}
    with open(cfg_path, "w") as fh:
        json.dump(conf, fh)
    print(json.dumps({{"status": "ok"}}))
    sys.exit(0)
if args[:1] == ["health"]:
    conf = json.load(open(cfg_path))
    req = urllib.request.Request(conf["profiles"][conf["active"]]["url"] + "/health")
    req.add_header("Authorization", "Bearer " + conf["profiles"][conf["active"]]["api_key"])
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            healthy = resp.status == 200
            print(json.dumps({{"ok": healthy, "result": {{"healthy": healthy}}}}))
            sys.exit(0 if healthy else 1)
    except Exception:
        print(json.dumps({{"ok": False, "result": {{"healthy": False}}}}))
        sys.exit(1)
print(json.dumps({{"error": "unknown command"}}), file=sys.stderr)
sys.exit(2)
'''


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离沙盒:HOME、ovcli.conf、user_api_key、可执行 ov stub 全在 tmp_path。"""
    home = tmp_path / "home"
    (home).mkdir()
    state = tmp_path / "state"
    state.mkdir()
    cli_cfg = state / "ovcli.conf"
    user_key = state / "secrets" / "user_api_key"
    ov_bin = tmp_path / "bin" / "ov"
    ov_bin.parent.mkdir()
    ov_bin.write_text(OV_STUB)
    ov_bin.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))  # 全局纪律:测试绝不写真实 HOME
    return type("Sandbox", (), {
        "home": home, "state": state, "cli_cfg": cli_cfg,
        "user_key": user_key, "ov_bin": str(ov_bin), "tmp": tmp_path,
    })()


@pytest.fixture()
def ovserver():
    fake = FakeOvServer()
    yield fake
    fake.close()


def run_bootstrap(sb, ovserver, root_key=ROOT_KEY, url=None):
    return ba.run(
        root_key=root_key,
        base_url=url or ovserver.url,
        cli_config_path=str(sb.cli_cfg),
        user_key_path=str(sb.user_key),
        ov_bin=sb.ov_bin,
        env=dict(os.environ),
    )


def assert_no_key_material(capsys):
    out = capsys.readouterr()
    assert ROOT_KEY not in out.out + out.err
    assert USER_KEY not in out.out + out.err


# ---------- 用例 ----------


def test_missing_root_key_errors(sandbox, ovserver, capsys):
    rc = run_bootstrap(sandbox, ovserver, root_key="")
    assert rc != 0
    assert not sandbox.user_key.exists()
    assert_no_key_material(capsys)


def test_first_init_via_user_registration(sandbox, ovserver, capsys):
    """v0.5.2 首启自动建空壳 default 账户:POST /accounts 409 后回退注册 user。"""
    rc = run_bootstrap(sandbox, ovserver)
    assert rc == 0
    assert sandbox.user_key.read_text() == USER_KEY
    assert stat.S_IMODE(sandbox.user_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(sandbox.cli_cfg.stat().st_mode) == 0o600
    assert ovserver.accounts_calls == 1 and ovserver.users_calls == 1
    # CLI 首跑需要语言设置,bootstrap 必须先行设置
    settings = sandbox.cli_cfg.parent / "ovcli.settings.conf"
    assert settings.exists() and "zh-CN" in settings.read_text()
    assert_no_key_material(capsys)


def test_first_init_when_no_shell_account(sandbox, ovserver, capsys):
    """无空壳账户的服务器(未来版本行为):POST /accounts 直接成功。"""
    ovserver.shell_account_exists = False
    rc = run_bootstrap(sandbox, ovserver)
    assert rc == 0
    assert sandbox.user_key.read_text() == USER_KEY
    assert ovserver.accounts_calls == 1 and ovserver.users_calls == 0
    assert_no_key_material(capsys)


def test_rerun_idempotent_no_second_account(sandbox, ovserver):
    assert run_bootstrap(sandbox, ovserver) == 0
    assert run_bootstrap(sandbox, ovserver) == 0
    assert ovserver.accounts_calls == 1  # 幂等:不重复创建账户


def test_account_and_user_exist_but_key_missing_errors(sandbox, ovserver, capsys):
    """账户与 user 都已存在而本地 key 丢失:进入人工恢复,不重复创建。"""
    ovserver.user_exists = True
    rc = run_bootstrap(sandbox, ovserver)
    assert rc != 0
    assert not sandbox.user_key.exists()   # 不伪造恢复
    assert not sandbox.cli_cfg.exists()
    assert_no_key_material(capsys)


def test_user_key_missing_from_response_errors(sandbox, ovserver):
    ovserver.drop_user_key = True
    rc = run_bootstrap(sandbox, ovserver)
    assert rc != 0
    assert not sandbox.user_key.exists()


def test_invalid_root_key_errors(sandbox, ovserver, capsys):
    rc = run_bootstrap(sandbox, ovserver, root_key="wrong-root-key")
    assert rc != 0
    assert not sandbox.user_key.exists()
    assert_no_key_material(capsys)


def test_existing_files_with_dead_key_errors_without_recreating(sandbox, ovserver):
    assert run_bootstrap(sandbox, ovserver) == 0  # 正常初始化
    ovserver.accepted_user_key = "rotated-away-key"  # 服务端已换 key
    rc = run_bootstrap(sandbox, ovserver)
    assert rc != 0
    assert ovserver.accounts_calls == 1  # 不重复创建账户,进入人工恢复


def test_server_unreachable_errors(sandbox):
    rc = run_bootstrap(
        sandbox, None, url="http://127.0.0.1:1"
    )
    assert rc != 0
    assert not sandbox.user_key.exists()


def test_secrets_dir_created_with_tight_mode(sandbox, ovserver):
    assert run_bootstrap(sandbox, ovserver) == 0
    secrets_dir = sandbox.user_key.parent
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700


def test_cli_cfg_without_user_key_errors_without_creating(sandbox, ovserver):
    """ovcli.conf 存在但 user_api_key 缺失:进入人工恢复,不发起任何创建调用。"""
    sandbox.cli_cfg.write_text('{"active": "local"}\n')
    rc = run_bootstrap(sandbox, ovserver)
    assert rc != 0
    assert ovserver.accounts_calls == 0 and ovserver.users_calls == 0
    assert not sandbox.user_key.exists()


def test_recovers_after_cli_config_failure(sandbox, ovserver, monkeypatch):
    monkeypatch.setenv("OV_STUB_CONFIG_FAIL", "1")
    rc = run_bootstrap(sandbox, ovserver)
    assert rc != 0                       # CLI 配置失败:user_key 已落盘但 ovcli.conf 缺失
    assert sandbox.user_key.exists()
    assert not sandbox.cli_cfg.exists()
    monkeypatch.delenv("OV_STUB_CONFIG_FAIL")
    assert run_bootstrap(sandbox, ovserver) == 0  # 重跑:用已有 user_key 补建配置
    assert sandbox.cli_cfg.exists()
    assert ovserver.accounts_calls == 1  # 不重复创建账户
