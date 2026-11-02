"""bootstrap_auth.py 单元测试(设计 §9.1.4)。

全部使用临时沙盒(HOME、CLI 配置、密钥文件)与进程内假 OpenViking HTTP
server;ov CLI 用可执行 stub 替身(真实 CLI 在部署验收时联调)。
覆盖:首次初始化、重跑幂等、账户已存在但丢 key、root/user key 无效、
服务不可达、输出不含任何密钥。
"""
import http.client
import json
import os
import stat
import subprocess
import threading
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
        self.success_status = 200
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
                self._json(server.success_status, {"status": "ok", "result": result})

            def do_GET(self):
                if self.path != "/health":
                    self._json(404, {"error": "not found"})
                    return
                auth = self.headers.get("Authorization")
                if auth not in (
                    f"Bearer {server.accepted_user_key}",
                    f"Bearer {ROOT_KEY}",  # 真实 /health 也接受 ROOT;用于覆盖 root-fallback
                ):
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
    # 模拟真实 CLI:语言设置固定在 $HOME/.openviking/ovcli.settings.conf。
    settings_dir = os.path.join(os.environ["HOME"], ".openviking")
    os.makedirs(settings_dir, exist_ok=True)
    settings = os.path.join(settings_dir, "ovcli.settings.conf")
    with open(settings, "w") as fh:
        json.dump({{"language": args[1] if len(args) > 1 else "en"}}, fh)
    sys.exit(0)
if args[:3] == ["config", "add", "custom"]:
    if os.environ.get("OV_STUB_ARGS_FILE"):
        with open(os.environ["OV_STUB_ARGS_FILE"], "w") as fh:
            fh.write(" ".join(args))
    if os.environ.get("OV_STUB_CONFIG_FAIL") == "1":
        sys.exit(3)
    url = args[args.index("--url") + 1]
    api_env = (
        args[args.index("--api-key-env") + 1]
        if "--api-key-env" in args else "OPENVIKING_USER_API_KEY"
    )
    key = os.environ[api_env]
    conf = {{"api_key": key, "url": url}}
    if "--root-api-key-env" in args:
        root_env = args[args.index("--root-api-key-env") + 1]
        root_key = os.environ.get(root_env)
        if root_key:
            conf["root_api_key"] = root_key
    with open(cfg_path, "w") as fh:
        json.dump(conf, fh)
    print(json.dumps({{"status": "ok"}}))
    sys.exit(0)
if args[:1] == ["health"]:
    settings = os.path.join(os.environ["HOME"], ".openviking", "ovcli.settings.conf")
    if not os.path.exists(settings):
        print("OpenViking needs a display language before running commands.", file=sys.stderr)
        sys.exit(2)
    conf = json.load(open(cfg_path))
    url = conf.get("url") or os.environ.get("OV_STUB_URL") or "http://127.0.0.1:1933"
    key = conf.get("api_key")
    role = "admin"
    if not key:
        # 模拟真实 CLI:api_key 缺失时回退 root_api_key,服务端返回 role=root。
        key = conf.get("root_api_key")
        role = "root"
    req = urllib.request.Request(url + "/health")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            healthy = resp.status == 200
            print(json.dumps({{"ok": healthy, "result": {{"healthy": healthy, "account_id": "default", "user_id": "default", "role": role}}}}))
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
    settings = sandbox.home / ".openviking" / "ovcli.settings.conf"
    assert settings.exists() and "zh-CN" in settings.read_text()
    # bootstrap 须固化 CLI timeout(README 运维结论,重建后免手工补写)
    cli_conf = json.loads(sandbox.cli_cfg.read_text())
    assert cli_conf.get("timeout") == 300
    # 真实 CLI 配置是平铺 api_key/root_api_key,不是 profiles 嵌套形态。
    assert cli_conf.get("api_key") == USER_KEY
    assert cli_conf.get("root_api_key") == ROOT_KEY
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


def test_rerun_rebuilds_missing_language_settings(sandbox, ovserver):
    """部分恢复缺 ovcli.settings.conf 时,幂等路径须先重建语言设置,不能误报凭据无效。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    settings = sandbox.home / ".openviking" / "ovcli.settings.conf"
    settings.unlink()
    assert run_bootstrap(sandbox, ovserver) == 0
    assert settings.exists()


def test_saved_profile_gets_timeout(sandbox, ovserver):
    """ov config add --name local 的 saved profile 也要补 timeout。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    saved.write_text(json.dumps({"api_key": USER_KEY}))
    assert run_bootstrap(sandbox, ovserver) == 0
    assert json.loads(saved.read_text())["timeout"] == 300


def test_saved_profile_path_is_ovcli_conf_name(sandbox, ovserver):
    """saved profile 固定为同目录 ovcli.conf.<name>,与 active 文件名无关。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    active = sandbox.cli_cfg.parent / "ovcli.test.conf"
    active.write_text(json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY}))
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    saved.write_text(
        json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY, "timeout": 60})
    )
    assert ba._ensure_cli_timeout(str(active))
    assert json.loads(active.read_text())["timeout"] == 300
    assert json.loads(saved.read_text())["timeout"] == 300


def test_timeout_below_minimum_is_raised_and_mode_tightened(sandbox, ovserver):
    """timeout=60 不足且 644 配置必须被修正为 300/600,而不是只检查存在性。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    cfg = json.loads(sandbox.cli_cfg.read_text())
    cfg["timeout"] = 60
    sandbox.cli_cfg.write_text(json.dumps(cfg))
    os.chmod(sandbox.cli_cfg, 0o644)
    assert run_bootstrap(sandbox, ovserver) == 0
    assert json.loads(sandbox.cli_cfg.read_text())["timeout"] == 300
    assert stat.S_IMODE(sandbox.cli_cfg.stat().st_mode) == 0o600


def test_recovery_uses_force_for_saved_profile(sandbox, ovserver, monkeypatch):
    """active 配置缺失而 saved profile 可能 stale 时,恢复路径须允许 --force。"""
    args_file = sandbox.tmp / "config-add-args"
    monkeypatch.setenv("OV_STUB_ARGS_FILE", str(args_file))
    assert run_bootstrap(sandbox, ovserver) == 0
    sandbox.cli_cfg.unlink()  # active 配置丢失,saved profile/用户 key 仍在
    assert run_bootstrap(sandbox, ovserver) == 0
    assert "--force" in args_file.read_text()


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


def test_user_key_write_failure_enters_manual_recovery(
    sandbox, ovserver, monkeypatch, capsys
):
    """user 已创建但 key 落盘失败:不得抛 traceback,须提示轮换后人工恢复。"""
    def failing_replace(*args):
        raise OSError("disk full")

    monkeypatch.setattr(ba.os, "replace", failing_replace)
    rc = run_bootstrap(sandbox, ovserver)

    assert rc == ba.RC_MANUAL_RECOVERY
    assert not sandbox.user_key.exists()
    assert ovserver.users_calls == 1  # user 已创建,不得重复创建
    captured = capsys.readouterr()
    assert "/api/v1/admin/accounts/default/users/default/key" in captured.err
    assert "Traceback" not in captured.err
    assert ROOT_KEY not in captured.out + captured.err
    assert USER_KEY not in captured.out + captured.err


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


def test_non_200_success_is_not_treated_as_completed(sandbox, ovserver):
    ovserver.success_status = 202
    rc = run_bootstrap(sandbox, ovserver)
    assert rc == ba.RC_UPSTREAM
    assert not sandbox.user_key.exists()


def test_truncated_admin_response_is_controlled_failure(monkeypatch):
    class TruncatedResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"{}")

    monkeypatch.setattr(
        ba.urllib.request, "urlopen", lambda *args, **kwargs: TruncatedResponse()
    )
    assert ba._post_json("http://127.0.0.1:1", {}, ROOT_KEY) == (0, None)


def test_missing_ov_binary_returns_structured_error(sandbox, ovserver, capsys):
    missing = sandbox.tmp / "bin" / "missing-ov"
    rc = ba.run(
        ROOT_KEY, ovserver.url, str(sandbox.cli_cfg), str(sandbox.user_key),
        ov_bin=str(missing), env=dict(os.environ),
    )
    assert rc == ba.RC_CLI_CONFIG
    assert sandbox.user_key.exists()  # 保留恢复所需 key,但不伪报初始化成功
    assert_no_key_material(capsys)


def test_health_requires_default_identity(monkeypatch, sandbox):
    def health_with_identity(user_id="default", account_id="default", role="admin"):
        return subprocess.CompletedProcess(
            ["ov", "health"], 0,
            stdout=json.dumps({
                "ok": True,
                "result": {
                    "healthy": True, "account_id": account_id,
                    "user_id": user_id, "role": role,
                },
            }).encode(),
        )

    good_cfg = json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY})
    sandbox.cli_cfg.write_text(good_cfg)
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity())
    assert ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})
    # ROOT key 也返回 default/default;role=root 必须判失败。
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity(role="root"))
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity(user_id="test"))
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})
    # CLI 配置里的 api_key 必须与本地 stored user key 一致。
    sandbox.cli_cfg.write_text(json.dumps({"api_key": "different-key", "root_api_key": ROOT_KEY}))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity())
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})
    # root key 轮换后,配置里残留旧 root_api_key 也必须判失败。
    sandbox.cli_cfg.write_text(json.dumps({"api_key": USER_KEY, "root_api_key": "stale-root"}))
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})
    # 缺 role 的结果同样失败。
    sandbox.cli_cfg.write_text(good_cfg)
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args: subprocess.CompletedProcess(
            ["ov", "health"], 0,
            stdout=json.dumps({"ok": True, "result": {"healthy": True}}).encode(),
        ),
    )
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})


def test_health_rejects_root_only_config(monkeypatch, sandbox):
    """api_key 缺失时 CLI 回退 root_api_key;health 的 default/default 不构成通过。"""
    sandbox.cli_cfg.write_text(json.dumps({"root_api_key": ROOT_KEY}))
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args: subprocess.CompletedProcess(
            ["ov", "health"], 0,
            stdout=json.dumps({
                "ok": True,
                "result": {
                    "healthy": True, "account_id": "default",
                    "user_id": "default", "role": "root",
                },
            }).encode(),
        ),
    )
    assert not ba._ov_health_ok("ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {})


def test_timeout_write_failure_is_reported_and_idempotent_retry_repairs(
    sandbox, ovserver, monkeypatch
):
    assert run_bootstrap(sandbox, ovserver) == 0
    cfg = json.loads(sandbox.cli_cfg.read_text())
    del cfg["timeout"]
    sandbox.cli_cfg.write_text(json.dumps(cfg))

    real_replace = ba.os.replace
    monkeypatch.setattr(ba.os, "replace", lambda *args: (_ for _ in ()).throw(OSError()))
    assert run_bootstrap(sandbox, ovserver) == ba.RC_CLI_CONFIG
    assert "timeout" not in json.loads(sandbox.cli_cfg.read_text())

    monkeypatch.setattr(ba.os, "replace", real_replace)
    assert run_bootstrap(sandbox, ovserver) == 0
    assert json.loads(sandbox.cli_cfg.read_text())["timeout"] == 300


def test_atomic_write_replaces_symlink_without_following(sandbox):
    """目标路径是符号链接时,os.replace 应替换链接本身而不写入其指向的文件。"""
    victim = sandbox.tmp / "victim"
    victim.write_text("unchanged")
    target = sandbox.tmp / "secret"
    target.symlink_to(victim)

    ba._atomic_write_secret(target, "new-secret\n")

    assert victim.read_text() == "unchanged"
    assert not target.is_symlink()
    assert target.read_text() == "new-secret\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_stale_predictable_tmp_symlink_is_not_followed(sandbox, ovserver):
    """预置可猜临时路径的符号链接时,完整 bootstrap 不得把 key/配置写入其目标。"""
    victim = sandbox.tmp / "victim"
    victim.write_text("unchanged")
    (sandbox.cli_cfg.parent / "ovcli.conf.tmp").symlink_to(victim)
    assert run_bootstrap(sandbox, ovserver) == 0
    assert victim.read_text() == "unchanged"
    assert not sandbox.cli_cfg.is_symlink()
    assert json.loads(sandbox.cli_cfg.read_text())["timeout"] == 300
