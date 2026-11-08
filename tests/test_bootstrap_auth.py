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
import sys
from pathlib import Path

import pytest

import _http_fakes
import bootstrap_auth as ba

REPO = Path(__file__).resolve().parents[1]
ROOT_KEY = "ROOTKEY-test-0000000000000000000000000000"
USER_KEY = "USERKEY-test-1111111111111111111111111111"
TEST_KEY = "USERKEY-test-2222222222222222222222222222"
ROTATED_KEY = "USERKEY-rotated-333333333333333333333333"


# ---------- 假 OpenViking server(骨架见 tests/_http_fakes.py) ----------


class FakeOvServer(_http_fakes.FakeServer):
    """实现账户初始化、任意 user 注册/轮换与健康探测。"""

    def __init__(self):
        self.shell_account_exists = True   # v0.5.2 服务端首启行为
        self.user_exists = False
        self.accepted_user_key = None
        self.accounts_calls = 0
        self.users_calls = 0
        self.rotation_calls = 0
        self.drop_user_key = False  # 模拟 trusted 模式缺 user_key 字段
        self.success_status = 200
        self.ok_envelope = True  # False:200+非 ok 信封(RC 阶梯回归用)
        self.users = {}
        self.last_user_id = None
        self.last_role = None
        server = self

        class Handler(_http_fakes.FakeBaseHandler):
            def _auth_root(self):
                if self.headers.get("Authorization") == f"Bearer {ROOT_KEY}":
                    return True
                self._json(401, {"status": "error", "message": "invalid root key"})
                return False

            def _body(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length) or b"{}")

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
                    body = self._body()
                    uid = body["user_id"]
                    server.last_user_id = uid
                    server.last_role = body["role"]
                    if (uid == "default" and server.user_exists) or uid in server.users:
                        self._json(409, {
                            "status": "error",
                            "error": {"code": "ALREADY_EXISTS",
                                      "message": f"User already exists: {uid}"},
                        })
                        return
                    if uid == "default":
                        server.user_exists = True
                    self._register_result({
                        "account_id": "default", "user_id": uid, "role": body["role"],
                    })
                    return
                prefix = "/api/v1/admin/accounts/default/users/"
                if self.path.startswith(prefix) and self.path.endswith("/key"):
                    server.rotation_calls += 1
                    if not self._auth_root():
                        return
                    uid = self.path[len(prefix):-len("/key")]
                    server.users[uid] = ROTATED_KEY
                    if uid == "default":
                        server.accepted_user_key = ROTATED_KEY
                    self._json(200, {"status": "ok", "result": {
                        "account_id": "default", "user_id": uid,
                        "user_key": ROTATED_KEY,
                    }})
                    return
                self._json(404, {"error": "not found"})

            def _register_result(self, result):
                uid = result.get("user_id") or result.get("admin_user_id")
                if not server.drop_user_key:
                    key = USER_KEY if uid == "default" else TEST_KEY
                    result["user_key"] = key
                    server.users[uid] = key
                    if uid == "default":
                        server.accepted_user_key = key
                self._json(server.success_status, {
                    "status": "ok" if server.ok_envelope else "error",
                    "result": result,
                })

            def do_GET(self):
                if self.path != "/health":
                    self._json(404, {"error": "not found"})
                    return
                auth = self.headers.get("Authorization")
                valid = {
                    f"Bearer {key}" for uid, key in server.users.items() if uid != "default"
                }
                valid.update((f"Bearer {server.accepted_user_key}", f"Bearer {ROOT_KEY}"))
                if auth not in valid:
                    self._json(401, {"status": "error"})
                    return
                self._json(200, {"status": "ok", "healthy": True})

        super().__init__(Handler)


# ---------- ov CLI stub:读 env 密钥、写沙盒 ovcli.conf、做真实认证探测 ----------

OV_STUB = f'''#!/usr/bin/env python3
import json, os, sys, urllib.request, urllib.error
# 探测与生产同纪律:禁代理直连 loopback,防止测试环境 http_proxy 劫持
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
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
    # 模拟真实 CLI:config add --name <name> 额外写同目录 saved profile
    # ovcli.conf.<name>。2026-11-03 检视前 stub 从不写它,bootstrap 的
    # saved-profile 契约(固定名、config add 副作用写入)只被手工文件验证。
    name = args[args.index("--name") + 1] if "--name" in args else "local"
    saved_path = os.path.join(os.path.dirname(cfg_path), "ovcli.conf." + name)
    with open(saved_path, "w") as fh:
        json.dump(conf, fh)
    print(json.dumps({{"status": "ok"}}))
    sys.exit(0)
if args[:1] == ["health"]:
    settings = os.path.join(os.environ["HOME"], ".openviking", "ovcli.settings.conf")
    if not os.path.exists(settings):
        print("OpenViking needs a display language before running commands.", file=sys.stderr)
        sys.exit(2)
    conf = json.load(open(cfg_path))
    if os.environ.get("OV_STUB_HEALTH_AUDIT"):
        with open(os.environ["OV_STUB_HEALTH_AUDIT"], "w") as audit:
            json.dump({{"config": conf, "root_env_present": bool(os.environ.get("OPENVIKING_ROOT_API_KEY"))}}, audit)
    url = conf.get("url") or os.environ.get("OV_STUB_URL") or "http://127.0.0.1:1933"
    key = conf.get("api_key")
    role = "admin"
    user_id = "default"
    if key == "{TEST_KEY}" or key == "{ROTATED_KEY}":
        user_id = "test"
        role = "user"
    if not key:
        # 模拟真实 CLI:api_key 缺失时回退 root_api_key,服务端返回 role=root。
        key = conf.get("root_api_key")
        role = "root"
    req = urllib.request.Request(url + "/health")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with _opener.open(req, timeout=5) as resp:
            healthy = resp.status == 200
            print(json.dumps({{"ok": healthy, "result": {{"healthy": healthy, "account_id": "default", "user_id": user_id, "role": role}}}}))
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


def health_ok(sb, *args, **kwargs):
    """既有 default/admin 健康用例的显式参数样板。"""
    return getattr(ba, "_ov_health_ok")(
        *args,
        expected_account="default",
        expected_user="default",
        expected_role="admin",
        require_root_key=True,
        saved_profile_path=ba._saved_profile_path(args[1]),
        **kwargs,
    )


def fail_os_replace_under_sandbox(monkeypatch, sandbox):
    """只对沙盒内目标注入 os.replace 失败:ba.os 是全局 os 模块,无限补丁会
    波及补丁窗口内任何无关的 os.replace 调用(2026-11-03 检视前逐字复制了
    两份,现共享此一份)。恢复原始实现用:
    monkeypatch.setattr(ba.os, "replace", ba.os.replace 的原始函数)。"""
    real_replace = ba.os.replace

    def failing_replace(src, dst, *args):
        if str(dst).startswith(str(sandbox.tmp)):
            raise OSError("disk full")
        return real_replace(src, dst, *args)

    monkeypatch.setattr(ba.os, "replace", failing_replace)
    return real_replace


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
    # config add --name local 须副作用写 saved profile 且同样补 timeout
    # (2026-11-03 检视:此前 stub 从不写它,该契约仅被手工文件验证)。
    saved_conf = json.loads(
        (sandbox.cli_cfg.parent / "ovcli.conf.local").read_text()
    )
    assert saved_conf.get("api_key") == USER_KEY
    assert saved_conf.get("timeout") == 300
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
    saved.write_text(json.dumps({
        "api_key": USER_KEY, "root_api_key": ROOT_KEY, "url": ovserver.url,
    }))
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


def test_matching_saved_profile_restores_interrupted_active(sandbox, ovserver):
    """config add 中断留下坏 active 时,可由完全匹配的 saved 原子恢复。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    saved_cfg = json.loads(sandbox.cli_cfg.read_text())
    saved.write_text(json.dumps(saved_cfg))
    sandbox.cli_cfg.write_text('{"partial":')
    assert run_bootstrap(sandbox, ovserver) == 0
    restored = json.loads(sandbox.cli_cfg.read_text())
    assert restored["api_key"] == USER_KEY
    assert restored["url"] == ovserver.url


def test_recovery_does_not_force_overwrite_saved_profile(sandbox, ovserver, monkeypatch):
    """active 缺失时不得用 --force 覆盖可能更新的 saved profile。"""
    args_file = sandbox.tmp / "config-add-args"
    monkeypatch.setenv("OV_STUB_ARGS_FILE", str(args_file))
    assert run_bootstrap(sandbox, ovserver) == 0
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    saved.write_text(json.dumps({
        "api_key": "newer-rotated-key", "root_api_key": ROOT_KEY, "url": ovserver.url,
    }))
    sandbox.cli_cfg.unlink()
    before = saved.read_bytes()
    assert run_bootstrap(sandbox, ovserver) != 0
    assert saved.read_bytes() == before
    assert "--force" not in args_file.read_text()


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
    fail_os_replace_under_sandbox(monkeypatch, sandbox)
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
    """首次初始化遇连接失败:必须判 RC_UPSTREAM(可重试),不得断言宽泛的
    rc != 0 而放过误判人工轮换的回归(2026-11-03 检视收紧)。"""
    rc = run_bootstrap(
        sandbox, None, url="http://127.0.0.1:1"
    )
    assert rc == ba.RC_UPSTREAM
    assert not sandbox.user_key.exists()


def test_transient_outage_in_idempotent_path_is_upstream_not_manual(
    sandbox, ovserver
):
    """凭据有效但服务暂不可达:判 RC_UPSTREAM(可重试),不得误导为
    "认证无效,需人工恢复/轮换"(2026-11-02 检视)。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    rc = run_bootstrap(sandbox, None, url="http://127.0.0.1:1")
    assert rc == ba.RC_UPSTREAM
    assert ovserver.accounts_calls == 1  # 不重复创建账户


def test_drifted_saved_profile_is_not_mutated_before_manual_recovery(
    sandbox, ovserver
):
    """saved profile 漂移时,幂等路径必须先核查后写回:不得先给它补
    timeout/chmod 再报人工恢复(保留现场)。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    cfg = json.loads(sandbox.cli_cfg.read_text())
    drifted = {k: v for k, v in cfg.items() if k != "timeout"}
    drifted["api_key"] = "older-rotated-key"
    saved.write_text(json.dumps(drifted))
    os.chmod(saved, 0o644)

    assert run_bootstrap(sandbox, ovserver) == ba.RC_MANUAL_RECOVERY

    # 原样保留:内容未追加 timeout,权限未被收紧
    assert json.loads(saved.read_text()) == drifted
    assert stat.S_IMODE(saved.stat().st_mode) == 0o644


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


def test_first_init_non_ok_envelope_is_no_user_key(sandbox, ovserver, capsys):
    """200+非 ok 信封与公共解析器同一映射:RC_NO_USER_KEY,不断言"已存在"。

    RC 阶梯统一(2026-11-07 用户决策):此路径原映射 RC_MANUAL_RECOVERY 且文案
    断言 default 账户/user 已存在——信封异常不是"已存在"的证据。"""
    ovserver.ok_envelope = False
    rc = run_bootstrap(sandbox, ovserver)

    captured = capsys.readouterr()
    assert rc == ba.RC_NO_USER_KEY
    assert not sandbox.user_key.exists()
    assert ovserver.users_calls == 1  # 只注册一次,无重试循环
    assert "信封非 ok" in captured.err
    assert "已存在" not in captured.err
    assert ROOT_KEY not in captured.out + captured.err
    assert USER_KEY not in captured.out + captured.err


def test_truncated_admin_response_is_controlled_failure(monkeypatch):
    class TruncatedResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            raise http.client.IncompleteRead(b"{}")

    monkeypatch.setattr(
        ba, "_direct_urlopen", lambda *args, **kwargs: TruncatedResponse()
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

    good_cfg = json.dumps({
        "url": ba.DEFAULT_BASE_URL, "api_key": USER_KEY, "root_api_key": ROOT_KEY,
    })
    sandbox.cli_cfg.write_text(good_cfg)
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity())
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "ok"
    # ROOT key 也返回 default/default;role=root 必须判失败。
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity(role="root"))
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    # 本部署 default 是运维 ADMIN;意外降级为 USER 不能视为初始化成功。
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity(role="user"))
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity(user_id="test"))
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    # CLI 配置里的 api_key 必须与本地 stored user key 一致。
    sandbox.cli_cfg.write_text(json.dumps({"api_key": "different-key", "root_api_key": ROOT_KEY}))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: health_with_identity())
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    # root key 轮换后,配置里残留旧 root_api_key 也必须判失败。
    sandbox.cli_cfg.write_text(json.dumps({"api_key": USER_KEY, "root_api_key": "stale-root"}))
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    # 缺 role 的结果同样失败。
    sandbox.cli_cfg.write_text(good_cfg)
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args: subprocess.CompletedProcess(
            ["ov", "health"], 0,
            stdout=json.dumps({"ok": True, "result": {"healthy": True}}).encode(),
        ),
    )
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"
    # 顶层(非嵌套 result)信封无生产者:按 fail-closed 一律判失败
    # (2026-11-03 检视移除了无依据的顶层兼容回退)。
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args: subprocess.CompletedProcess(
            ["ov", "health"], 0,
            stdout=json.dumps({
                "ok": True, "healthy": True, "account_id": "default",
                "user_id": "default", "role": "admin",
            }).encode(),
        ),
    )
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) == "invalid"


def test_health_rejects_url_and_saved_profile_drift(monkeypatch, sandbox):
    healthy = subprocess.CompletedProcess(
        ["ov", "health"], 0,
        stdout=json.dumps({
            "ok": True,
            "result": {
                "healthy": True, "account_id": "default",
                "user_id": "default", "role": "admin",
            },
        }).encode(),
    )
    monkeypatch.setattr(ba, "_run_ov", lambda *args: healthy)
    expected = "http://127.0.0.1:21933"
    good = {"api_key": USER_KEY, "root_api_key": ROOT_KEY, "url": expected}
    sandbox.cli_cfg.write_text(json.dumps(good))
    assert health_ok(sandbox,
        "ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}, base_url=expected,
    ) == "ok"
    sandbox.cli_cfg.write_text(json.dumps({**good, "url": "http://127.0.0.1:1933"}))
    assert health_ok(sandbox,
        "ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}, base_url=expected,
    ) == "invalid"
    sandbox.cli_cfg.write_text(json.dumps(good))
    saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    saved.write_text(json.dumps({**good, "api_key": "stale-key"}))
    assert health_ok(sandbox,
        "ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}, base_url=expected,
    ) == "invalid"


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
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) \
        == "invalid"


def test_health_timeout_is_distinguished_from_invalid(monkeypatch, sandbox):
    """ov health 超时(_run_ov 折叠为 124)必须可与服务端拒绝区分:
    超时不是凭据问题,不得把有效密钥推向人工轮换(2026-11-03 检视)。"""
    sandbox.cli_cfg.write_text(
        json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY})
    )
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args, **kwargs: subprocess.CompletedProcess(["ov", "health"], 124),
    )
    assert health_ok(sandbox,"ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {}) \
        == "timeout"


def test_health_timeout_in_idempotent_path_is_upstream(sandbox, ovserver, monkeypatch):
    """幂等路径遇 ov health 超时:判 RC_UPSTREAM(可重试),不判人工恢复。"""
    assert run_bootstrap(sandbox, ovserver) == 0
    monkeypatch.setattr(
        ba, "_run_ov",
        lambda *args, **kwargs: subprocess.CompletedProcess(["ov", "health"], 124),
    )
    assert run_bootstrap(sandbox, ovserver) == ba.RC_UPSTREAM


def test_timeout_write_failure_is_reported_and_idempotent_retry_repairs(
    sandbox, ovserver, monkeypatch
):
    assert run_bootstrap(sandbox, ovserver) == 0
    cfg = json.loads(sandbox.cli_cfg.read_text())
    del cfg["timeout"]
    sandbox.cli_cfg.write_text(json.dumps(cfg))

    real_replace = fail_os_replace_under_sandbox(monkeypatch, sandbox)
    assert run_bootstrap(sandbox, ovserver) == ba.RC_CLI_CONFIG
    assert "timeout" not in json.loads(sandbox.cli_cfg.read_text())

    monkeypatch.setattr(ba.os, "replace", real_replace)
    assert run_bootstrap(sandbox, ovserver) == 0
    assert json.loads(sandbox.cli_cfg.read_text())["timeout"] == 300


def test_non_finite_timeout_is_repaired(sandbox):
    sandbox.cli_cfg.write_text('{"timeout": Infinity}')
    assert ba._ensure_timeout_file(str(sandbox.cli_cfg))
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


def test_atomic_write_leaves_no_temp_residue_and_ignores_stale_symlinks(
    sandbox, ovserver
):
    """mkstemp 临时名含 8 位随机后缀且以 O_CREAT|O_EXCL 打开:预置符号链接
    无法命中也无法被跟随(2026-11-03 检视:旧用例预置的 'ovcli.conf.tmp'
    从不在写入路径上,属空转断言;本用例改为固化命名契约与无残留)。"""
    victim = sandbox.tmp / "victim"
    victim.write_text("unchanged")
    # 可猜到的名字(含 mkstemp 前缀形态)都指向 victim:均不得被跟随
    (sandbox.cli_cfg.parent / "ovcli.conf.tmp").symlink_to(victim)
    (sandbox.cli_cfg.parent / ".ovcli.conf.ZZZZZZZZ").symlink_to(victim)
    assert run_bootstrap(sandbox, ovserver) == 0
    assert victim.read_text() == "unchanged"
    assert not sandbox.cli_cfg.is_symlink()
    assert json.loads(sandbox.cli_cfg.read_text())["timeout"] == 300
    # 完整运行后不得残留任何 .ovcli.conf.* 临时文件(预置符号链接除外:
    # mkstemp 的随机名不会命中它们,它们本就不该被动到)
    residue = [
        p for p in sandbox.cli_cfg.parent.glob(".ovcli.conf.*")
        if not p.is_symlink()
    ]
    assert residue == []


def test_atomic_write_leaves_existing_parent_mode_unchanged(sandbox):
    parent = sandbox.tmp / "existing-parent"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    target = parent / "secret"
    ba._atomic_write_secret(target, "test-secret")
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_provision_test_user_writes_key_without_cli_config(sandbox, ovserver, capsys):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_OK
    assert key_path.read_text() == TEST_KEY
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert ovserver.last_user_id == "test" and ovserver.last_role == "user"
    assert ovserver.users_calls == 1 and ovserver.accounts_calls == 0
    assert_no_key_material(capsys)


def test_provision_existing_local_key_does_not_create(sandbox, ovserver):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    key_path.parent.mkdir()
    key_path.write_text(TEST_KEY)
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_OK
    assert key_path.read_text() == TEST_KEY
    assert ovserver.users_calls == 0


def test_provision_existing_local_key_tightens_permissions(sandbox, ovserver):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    key_path.parent.mkdir()
    key_path.write_text(TEST_KEY)
    key_path.chmod(0o644)
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_OK
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert ovserver.users_calls == 0


def test_provision_existing_cli_config_tightens_permissions(sandbox, ovserver):
    config = sandbox.home / ".openviking" / "ovcli.conf"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor",
    }))
    config.chmod(0o644)
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert ovserver.users_calls == 0


def _write_provision_reuse_config(sandbox, ovserver, saved_content=None):
    """provision --cli-config 复用路径的共同前置:身份匹配、自带 timeout 的
    active(active 侧无需写回,隔离出 saved profile 的行为)+ 可选的
    同目录 saved profile(ovcli.conf.local)内容。"""
    config = sandbox.home / ".openviking" / "ovcli.conf"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor", "timeout": 300,
    }))
    saved = config.parent / "ovcli.conf.local"
    if saved_content is not None:
        saved.write_text(saved_content)
    return config, saved


def test_provision_reuse_syncs_matching_saved_profile_timeout(
    sandbox, ovserver, capsys
):
    config, saved = _write_provision_reuse_config(sandbox, ovserver, json.dumps({
        "url": ovserver.url, "api_key": "USERKEY-stale-9999999999999999999999",
        "account": "default", "user": "meteor",
    }))
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    saved_cfg = json.loads(saved.read_text())
    # 身份匹配即同步 timeout;陈旧 api_key 不阻止(离线不可见,措辞纪律:
    # 确认行只声称身份匹配,不得暗示凭据一致)
    assert saved_cfg["timeout"] == 300
    assert saved_cfg["api_key"] == "USERKEY-stale-9999999999999999999999"
    assert (
        "已确认 ovcli.conf.local 的 timeout 契约"
        "(身份匹配 url/account/user;api_key 未在线核验)" in capsys.readouterr().out
    )
    assert ovserver.users_calls == 0


def test_provision_reuse_saved_sufficient_timeout_confirmed_not_clobbered(
    sandbox, ovserver, capsys
):
    config, saved = _write_provision_reuse_config(sandbox, ovserver, json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor", "timeout": 600,
    }))
    before = saved.read_bytes()
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    # 已有充分 timeout 不覆盖(与 active 同一 _clamp_cfg_timeout 谓词)
    assert saved.read_bytes() == before
    assert "已确认 ovcli.conf.local 的 timeout 契约" in capsys.readouterr().out
    assert ovserver.users_calls == 0


def test_provision_reuse_drifted_saved_url_preserved_with_warning(
    sandbox, ovserver, capsys
):
    config, saved = _write_provision_reuse_config(sandbox, ovserver, json.dumps({
        "url": "http://127.0.0.1:1934", "api_key": TEST_KEY,
        "account": "default", "user": "meteor",
    }))
    before = saved.read_bytes()
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    # 漂移 saved:告警放行(2026-11-08 用户决策),字节级保留现场
    assert saved.read_bytes() == before
    assert (
        "警告:ovcli.conf.local 身份(url/account/user)与目标不一致,"
        "未同步 timeout,已原样保留" in capsys.readouterr().err
    )
    assert ovserver.users_calls == 0


def test_provision_reuse_saved_with_root_key_preserved_with_warning(
    sandbox, ovserver, capsys
):
    config, saved = _write_provision_reuse_config(sandbox, ovserver, json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor", "root_api_key": ROOT_KEY,
    }))
    before = saved.read_bytes()
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    # 含 root_api_key 的 saved:provision 域永不触碰(与 active 侧
    # _cli_config_for_output 同一防线),告警放行
    assert saved.read_bytes() == before
    assert (
        "警告:ovcli.conf.local 含 root_api_key,"
        "未同步 timeout,已原样保留" in capsys.readouterr().err
    )
    assert ovserver.users_calls == 0


def test_provision_reuse_unparseable_saved_preserved_with_warning(
    sandbox, ovserver, capsys
):
    config, saved = _write_provision_reuse_config(
        sandbox, ovserver, "{not-json",
    )
    before = saved.read_bytes()
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    # 不可解析的邻居文件不得阻断健康的 active 复用(直接换 _ensure_cli_timeout
    # 的天真方案在此会 RC_CLI_CONFIG 硬失败——本用例即钉住告警放行语义)
    assert saved.read_bytes() == before
    assert (
        "警告:ovcli.conf.local 无法解析,"
        "未同步 timeout,已原样保留" in capsys.readouterr().err
    )
    assert ovserver.users_calls == 0


def test_provision_reuse_saved_timeout_write_failure_hard_fails(
    sandbox, ovserver, monkeypatch, capsys
):
    config, saved = _write_provision_reuse_config(sandbox, ovserver, json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor",
    }))
    before = saved.read_bytes()
    fail_os_replace_under_sandbox(monkeypatch, sandbox)
    # 身份匹配(属本身份域)但写回失败:与 active 的 timeout 失败同一
    # RC_CLI_CONFIG 硬失败,不是告警放行
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_CLI_CONFIG
    assert saved.read_bytes() == before
    assert "错误:无法确保 ovcli.conf.local 的 timeout=300" in capsys.readouterr().err


def test_provision_reuse_without_saved_no_warning(sandbox, ovserver, capsys):
    config, _saved = _write_provision_reuse_config(sandbox, ovserver)
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    # 常见路径(saved 不存在)零新增噪音
    captured = capsys.readouterr()
    assert "警告" not in captured.err and "已确认" not in captured.out
    assert ovserver.users_calls == 0


def test_provision_conflict_requires_rotation_without_recreating(sandbox, ovserver):
    ovserver.users["test"] = "server-only-key"
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_MANUAL_RECOVERY
    assert ovserver.users_calls == 1 and ovserver.rotation_calls == 0
    assert not key_path.exists()


def test_provision_write_failure_recovers_only_via_rotation(
    sandbox, ovserver, monkeypatch
):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    real_replace = fail_os_replace_under_sandbox(monkeypatch, sandbox)
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_MANUAL_RECOVERY
    assert ovserver.users_calls == 1 and not key_path.exists()

    monkeypatch.setattr(ba.os, "replace", real_replace)
    assert ba.rotate_user(
        ROOT_KEY, ovserver.url, "test", user_key_path=str(key_path),
    ) == ba.RC_OK
    assert key_path.read_text() == ROTATED_KEY
    assert ovserver.users_calls == 1 and ovserver.rotation_calls == 1


def test_provision_meteor_writes_root_free_cli_config(sandbox, ovserver):
    config = sandbox.home / ".openviking" / "ovcli.conf"
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "meteor", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    cfg = json.loads(config.read_text())
    # provision 与 run()/_configure_cli 同一 timeout 契约(README:CLI 60s
    # 默认会让 `ov add-resource --wait` 在慢任务上 DEADLINE_EXCEEDED)。
    assert cfg == {
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor", "timeout": 300,
    }
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_provision_rejects_non_ok_envelope(sandbox, monkeypatch):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    monkeypatch.setattr(ba, "_register_user", lambda *args: (200, {
        "status": "error", "result": {"user_key": TEST_KEY},
    }))
    assert ba.provision_user(
        ROOT_KEY, "http://127.0.0.1:1933", "test", "user",
        user_key_path=str(key_path),
    ) != ba.RC_OK
    assert not key_path.exists()


def test_provision_requires_exactly_one_output(sandbox, ovserver):
    assert ba.provision_user(ROOT_KEY, ovserver.url, "test", "user") == ba.RC_BAD_ENV
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user",
        user_key_path=str(sandbox.user_key), cli_config_path=str(sandbox.cli_cfg),
    ) == ba.RC_BAD_ENV
    assert ovserver.users_calls == 0


def test_custom_profile_does_not_touch_default_saved_profile(sandbox, ovserver):
    assert run_bootstrap(sandbox, ovserver) == 0
    default_saved = sandbox.cli_cfg.parent / "ovcli.conf.local"
    before = default_saved.read_bytes()
    custom = sandbox.state / "ovcli.test.conf"
    assert ba._configure_cli(
        sandbox.ov_bin, ovserver.url, str(custom), ROOT_KEY, USER_KEY,
        dict(os.environ), profile_name="test",
    ) == ba.RC_OK
    assert custom.exists()
    assert (sandbox.state / "ovcli.conf.test").exists()
    assert default_saved.read_bytes() == before
    assert json.loads((sandbox.state / "ovcli.conf.test").read_text())["timeout"] == 300


def test_check_user_uses_temporary_root_free_config(sandbox, ovserver, monkeypatch):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", user_key_path=str(key_path),
    ) == ba.RC_OK
    config = sandbox.tmp / "container-tmp" / "ovcli.test.conf"
    audit = sandbox.tmp / "health-audit.json"
    monkeypatch.setenv("OV_STUB_HEALTH_AUDIT", str(audit))
    env = dict(os.environ)
    env[ba.ROOT_KEY_ENV] = ROOT_KEY
    assert ba.check_user(
        sandbox.ov_bin, ovserver.url, str(config), str(key_path), env,
        account_id="default", user_id="test", role="user",
    ) == ba.RC_OK
    observed = json.loads(audit.read_text())
    assert observed["config"] == {
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "test",
    }
    assert observed["root_env_present"] is False
    assert not config.exists()
    assert (sandbox.home / ".openviking" / "ovcli.settings.conf").exists()


def test_health_accepts_explicit_test_identity_without_saved_profile(
    monkeypatch, sandbox
):
    sandbox.cli_cfg.write_text(json.dumps({
        "url": "http://127.0.0.1:21933", "api_key": TEST_KEY,
        "account": "default", "user": "test",
    }))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: subprocess.CompletedProcess(
        ["ov", "health"], 0,
        stdout=json.dumps({"ok": True, "result": {
            "healthy": True, "account_id": "default", "user_id": "test", "role": "user",
        }}).encode(),
    ))
    assert ba._ov_health_ok(
        "ov", str(sandbox.cli_cfg), None, TEST_KEY, {},
        base_url="http://127.0.0.1:21933", expected_account="default",
        expected_user="test", expected_role="user", require_root_key=False,
        saved_profile_path=None,
    ) == "ok"


def test_health_rejects_root_material_in_root_free_config(monkeypatch, sandbox):
    sandbox.cli_cfg.write_text(json.dumps({
        "url": "http://127.0.0.1:21933", "api_key": TEST_KEY,
        "root_api_key": ROOT_KEY,
    }))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: subprocess.CompletedProcess(
        ["ov", "health"], 0,
        stdout=json.dumps({"ok": True, "result": {
            "healthy": True, "account_id": "default", "user_id": "test", "role": "user",
        }}).encode(),
    ))
    assert ba._ov_health_ok(
        "ov", str(sandbox.cli_cfg), None, TEST_KEY, {},
        base_url="http://127.0.0.1:21933", expected_account="default",
        expected_user="test", expected_role="user", require_root_key=False,
        saved_profile_path=None,
    ) == "invalid"


def test_health_requires_explicit_url(monkeypatch, sandbox):
    sandbox.cli_cfg.write_text(json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY}))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: subprocess.CompletedProcess(
        ["ov", "health"], 0,
        stdout=json.dumps({"ok": True, "result": {
            "healthy": True, "account_id": "default", "user_id": "default", "role": "admin",
        }}).encode(),
    ))
    assert ba._ov_health_ok(
        "ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {},
        base_url="http://127.0.0.1:21933", expected_account="default",
        expected_user="default", expected_role="admin", require_root_key=True,
        saved_profile_path=None,
    ) == "invalid"


def test_health_default_legacy_config_uses_documented_default_url(
    monkeypatch, sandbox
):
    sandbox.cli_cfg.write_text(json.dumps({"api_key": USER_KEY, "root_api_key": ROOT_KEY}))
    monkeypatch.setattr(ba, "_run_ov", lambda *args: subprocess.CompletedProcess(
        ["ov", "health"], 0,
        stdout=json.dumps({"ok": True, "result": {
            "healthy": True, "account_id": "default", "user_id": "default", "role": "admin",
        }}).encode(),
    ))
    assert ba._ov_health_ok(
        "ov", str(sandbox.cli_cfg), ROOT_KEY, USER_KEY, {},
        base_url=ba.DEFAULT_BASE_URL, expected_account="default",
        expected_user="default", expected_role="admin", require_root_key=True,
        saved_profile_path=None,
    ) == "ok"


def test_main_provision_user_subcommand(sandbox, ovserver, monkeypatch):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    monkeypatch.setenv(ba.ROOT_KEY_ENV, ROOT_KEY)
    monkeypatch.setenv("OPENVIKING_BOOTSTRAP_URL", ovserver.url)
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(sandbox.cli_cfg))
    monkeypatch.setenv("OPENVIKING_USER_KEY_FILE", str(sandbox.user_key))
    monkeypatch.setenv("OV_BIN", sandbox.ov_bin)
    monkeypatch.setattr(sys, "argv", [
        "bootstrap_auth.py", "provision-user", "--user-id", "test",
        "--role", "user", "--key-file", str(key_path),
    ])
    with pytest.raises(SystemExit) as exc:
        ba.main()
    assert exc.value.code == 0
    assert key_path.read_text() == TEST_KEY
    assert ovserver.last_user_id == "test"


@pytest.mark.parametrize("bad_id", ["../evil", "default?include_credentials=true#", "a/b", ""])
def test_main_rejects_unsafe_user_id(sandbox, ovserver, monkeypatch, bad_id):
    """--user-id 进入 admin API URL 路径与 check-user 临时配置路径:与
    e2e_user_isolation.sh 同一白名单,路径/查询改写字符一律 RC_BAD_ENV,
    且不发起任何网络调用(2026-11-07 sweep)。"""
    monkeypatch.setenv(ba.ROOT_KEY_ENV, ROOT_KEY)
    monkeypatch.setenv("OPENVIKING_BOOTSTRAP_URL", ovserver.url)
    monkeypatch.setattr(sys, "argv", [
        "bootstrap_auth.py", "rotate-user", "--user-id", bad_id,
        "--key-file", str(sandbox.state / "x"),
    ])
    with pytest.raises(SystemExit) as exc:
        ba.main()
    assert exc.value.code == ba.RC_BAD_ENV
    assert ovserver.rotation_calls == 0


def test_check_user_refuses_existing_config_without_deleting(sandbox, ovserver):
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    key_path.parent.mkdir()
    key_path.write_text(TEST_KEY)
    sandbox.cli_cfg.write_text("formal-config-must-survive")
    assert ba.check_user(
        sandbox.ov_bin, ovserver.url, str(sandbox.cli_cfg), str(key_path), {},
        account_id="default", user_id="test", role="user",
    ) == ba.RC_MANUAL_RECOVERY
    assert sandbox.cli_cfg.read_text() == "formal-config-must-survive"
    assert key_path.read_text() == TEST_KEY


def test_check_user_rejects_same_key_and_config_path(sandbox, ovserver):
    """同路径是纯参数错误(与 run() 同一守卫、同一 RC_BAD_ENV;2026-11-07
    检视:原 RC_MANUAL_RECOVERY 会误导操作者走人工恢复流程)。"""
    key_path = sandbox.state / "secrets" / "test_user_api_key"
    key_path.parent.mkdir()
    key_path.write_text(TEST_KEY)
    assert ba.check_user(
        sandbox.ov_bin, ovserver.url, str(key_path), str(key_path), {},
        account_id="default", user_id="test", role="user",
    ) == ba.RC_BAD_ENV
    assert key_path.read_text() == TEST_KEY


def test_rotate_cli_config_rejects_other_identity_without_remote_change(
    sandbox, ovserver
):
    sandbox.cli_cfg.write_text(json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "meteor",
    }))
    before = sandbox.cli_cfg.read_bytes()
    assert ba.rotate_user(
        ROOT_KEY, ovserver.url, "test", cli_config_path=str(sandbox.cli_cfg),
    ) == ba.RC_MANUAL_RECOVERY
    assert sandbox.cli_cfg.read_bytes() == before
    assert ovserver.rotation_calls == 0


def test_run_rejects_same_key_and_config_path(sandbox):
    """run() 与 check_user 同一防线:key 文件与 CLI 配置同路径会在 config add
    时被 JSON 原地覆盖,凭据静默损坏,必须在任何写入前拒绝(2026-11-06 检视)。"""
    shared = sandbox.state / "same-path"
    rc = ba.run(ROOT_KEY, "http://127.0.0.1:1933", str(shared), str(shared),
                ov_bin=sandbox.ov_bin, env=dict(os.environ))
    assert rc == ba.RC_BAD_ENV
    assert not shared.exists()


def test_run_strips_proxy_env_from_ov_children(sandbox):
    """ov 子进程携带 root/user key 的 Authorization,不得继承宿主代理设置;
    与 _DIRECT_OPENER/e2e curl --noproxy 同一纪律(2026-11-06 检视)。"""
    env = {"http_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
           "PATH": os.environ["PATH"]}
    proc = ba._run_ov("/usr/bin/env", [], str(sandbox.cli_cfg), None, None, env)
    assert proc.returncode == 0
    assert b"http_proxy" not in proc.stdout
    assert b"HTTPS_PROXY" not in proc.stdout


def test_response_user_key_rejects_non_string_key(monkeypatch, sandbox):
    """服务端回归导致 user_key 为 dict/int 时必须走 RC_NO_USER_KEY 受控路径,
    不得流到 fh.write 处抛 TypeError 逃出受控错误(2026-11-06 检视)。"""
    monkeypatch.setattr(ba, "_register_user", lambda *args: (
        200, {"status": "ok", "result": {"user_key": {"evil": 1}}},
    ))
    key_path = sandbox.state / "secrets" / "bad_type_key"
    rc = ba.provision_user(
        ROOT_KEY, "http://127.0.0.1:1933", "bad", "user",
        user_key_path=str(key_path),
    )
    assert rc == ba.RC_NO_USER_KEY
    assert not key_path.exists()


def test_first_init_surrogate_key_write_enters_manual_recovery(
    sandbox, ovserver, monkeypatch, capsys
):
    """服务端漂移返回含孤立代理字符的 user_key(字符串、过判型)时,落盘
    fh.write 抛 UnicodeEncodeError(ValueError 家族):run() 首启路径必须与
    provision/rotate 同一覆盖,走受控人工恢复而非 traceback 逃逸
    (2026-11-07 检视;原 except OSError 漏掉该类)。"""
    surrogate_key = json.loads('"\\ud800bad"')
    monkeypatch.setattr(ba, "_register_default_user", lambda *args, **kwargs: (
        200, {"status": "ok", "result": {"user_key": surrogate_key}},
    ))
    rc = run_bootstrap(sandbox, ovserver)
    captured = capsys.readouterr()
    assert rc == ba.RC_MANUAL_RECOVERY
    assert "Traceback" not in captured.err
    assert "落盘失败" in captured.err
    assert not sandbox.user_key.exists()


def test_provision_rejects_root_role(sandbox):
    """provision-user 不得产出第二个 root 身份(与 check_user/_ov_health_ok
    同一 role 矩阵防线;2026-11-06 检视)。"""
    key_path = sandbox.state / "secrets" / "root_role_key"
    assert ba.provision_user(
        ROOT_KEY, "http://127.0.0.1:1933", "svc", "root",
        user_key_path=str(key_path),
    ) == ba.RC_BAD_ENV
    assert not key_path.exists()


def test_provision_null_url_config_enters_manual_recovery(sandbox):
    """ovcli.conf 的 url 为 JSON null 时,旧实现 None.rstrip 抛 AttributeError
    逃出受控错误路径;现在必须按身份不符走人工恢复(2026-11-06 检视)。"""
    config = sandbox.state / "ovcli-null-url.conf"
    config.write_text(json.dumps({
        "url": None, "api_key": TEST_KEY,
        "account": "default", "user": "test",
    }))
    assert ba.provision_user(
        ROOT_KEY, "http://127.0.0.1:1933", "test", "user",
        cli_config_path=str(config),
    ) == ba.RC_MANUAL_RECOVERY


def test_provision_ensures_timeout_on_cli_config(sandbox, ovserver):
    """provision/rotate 的 --cli-config 输出与 run() 同一 timeout 契约;
    缺 timeout 的配置会在 `ov add-resource --wait` 处超时(2026-11-06 检视)。"""
    config = sandbox.state / "ovcli-timeout.conf"
    config.write_text(json.dumps({
        "url": ovserver.url, "api_key": TEST_KEY,
        "account": "default", "user": "test",
    }))
    assert ba.provision_user(
        ROOT_KEY, ovserver.url, "test", "user", cli_config_path=str(config),
    ) == ba.RC_OK
    assert json.loads(config.read_text())["timeout"] == 300
