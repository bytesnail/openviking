#!/usr/bin/env python
"""OpenViking api_key 模式认证初始化(设计 §7.2)。

在 OpenViking healthy 后于容器内执行一次:
1. root key 从环境读取,绝不打印;
2. 已有 user_api_key + ovcli.conf 时验证现有认证,幂等退出;
3. 首次初始化:root key 调 POST /api/v1/admin/accounts 创建 default 账户,
   原子写入 result.user_key(600),再以 --api-key-env/--root-api-key-env
   让 ov CLI 从子进程环境取密钥完成 config add,最后 ov health 验证;
4. 账户已存在但本地 key 缺失/失效:退出并要求人工恢复,不重复创建账户。

输出纪律:任何输出不含密钥值,失败只报步骤与退出码。
"""
import argparse
import http.client
import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RC_OK = 0
RC_BAD_ENV = 2
RC_UPSTREAM = 5
RC_INVALID_ROOT_KEY = 6
RC_NO_USER_KEY = 7
RC_CLI_CONFIG = 8
RC_AUTH_VERIFY = 9
RC_MANUAL_RECOVERY = 4

USER_KEY_ENV = "OPENVIKING_USER_API_KEY"
ROOT_KEY_ENV = "OPENVIKING_ROOT_API_KEY"
# 本地管理请求必须直连;默认 urllib opener 会继承 http_proxy,可能把
# root Authorization 或本机数据交给外部代理。
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向。

    urllib 默认对 301/302/303 的 POST 会转 GET 并原样携带 Authorization
    (含 root key)追到 Location 目标(实测确认)。返回 None 使 opener 以
    HTTPError(3xx) 报错,不发送后续请求。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect()
)
# 同型 _NoRedirect/_DIRECT_OPENER 在 scripts/env_file.py 与
# scripts/embedding_adapter.py 各有一份(python -I 隔离运行,无法共享 import);
# 对此处的任何加固必须同步另外两份,tests/test_env_file.py 有行为一致性断言。


def _direct_urlopen(request, timeout):
    return _DIRECT_OPENER.open(request, timeout=timeout)
# `ov config add --name local` 会额外写一个 saved profile(`ovcli.conf.local`);
# 健康检查/超时加固/恢复流程都必须把它当作 CLI 生成的文件处理,而非手工副本。
CLI_PROFILE_NAME = "local"
DEFAULT_BASE_URL = "http://127.0.0.1:1933"


def _atomic_write_secret(path, content):
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _exclusive_write_secret(path, content):
    """只创建不存在的 secret;绝不覆盖或清理调用者已有文件。"""
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _post_json(url, body, root_key, timeout=30):
    """POST JSON with root key; return (status:int, payload:dict|None); status=0 on connection failure."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {root_key}"},
            method="POST",
        )
        with _direct_urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read() or b"null"
    except urllib.error.HTTPError as err:
        try:
            raw = err.read() or b"null"
        except (http.client.HTTPException, OSError):
            return err.code, None
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        return err.code, payload
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError, UnicodeError):
        # 非法 base URL/非 Latin-1 key 会让 Request 构造抛 ValueError/UnicodeError;
        # 折叠为连接失败,保持"失败只报步骤与退出码"的契约。
        return 0, None
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None  # 200 但响应体非 JSON:按缺 result 处理,走 RC_NO_USER_KEY
    return status, payload


def _post_accounts(base_url, root_key, timeout=30):
    return _post_json(
        base_url.rstrip("/") + "/api/v1/admin/accounts",
        {"account_id": "default", "admin_user_id": "default"},
        root_key,
        timeout,
    )


def _register_user(base_url, root_key, user_id, role, timeout=30):
    return _post_json(
        base_url.rstrip("/") + "/api/v1/admin/accounts/default/users",
        {"user_id": user_id, "role": role},
        root_key,
        timeout,
    )


def _register_default_user(base_url, root_key, timeout=30):
    """Register the default admin after v0.5.2 first-start account creation."""
    return _register_user(base_url, root_key, "default", "admin", timeout)


def _rotate_user_key(base_url, root_key, user_id, timeout=30):
    return _post_json(
        base_url.rstrip("/")
        + f"/api/v1/admin/accounts/default/users/{user_id}/key",
        {},
        root_key,
        timeout,
    )


def _cli_config_for_output(path):
    if not os.path.exists(path):
        return {}
    cfg = _load_cli_config(path)
    if cfg is None or "root_api_key" in cfg:
        raise ValueError("CLI config is invalid or contains root_api_key")
    return cfg


def _write_user_output(user_key, base_url, user_id, user_key_path, cli_config_path):
    if user_key_path:
        _atomic_write_secret(Path(user_key_path), user_key)
        return
    cfg = _cli_config_for_output(cli_config_path)
    cfg.update({
        "url": base_url,
        "api_key": user_key,
        "account": "default",
        "user": user_id,
    })
    _atomic_write_secret(Path(cli_config_path), json.dumps(cfg))


def _response_user_key(status, payload):
    if status == 401 or status == 403:
        print("错误:root key 被服务端拒绝", file=sys.stderr)
        return RC_INVALID_ROOT_KEY, None
    if status == 409:
        print("错误:user 已存在但本地凭据缺失;禁止重复创建,请修复输出后轮换", file=sys.stderr)
        return RC_MANUAL_RECOVERY, None
    if status != 200:
        print(f"错误:user 管理请求失败(admin API status={status})", file=sys.stderr)
        return RC_UPSTREAM, None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        print("错误:user 管理响应信封非 ok,拒绝继续", file=sys.stderr)
        return RC_NO_USER_KEY, None
    result = payload.get("result")
    user_key = result.get("user_key") if isinstance(result, dict) else None
    if not user_key:
        print("错误:响应缺少 result.user_key,拒绝继续", file=sys.stderr)
        return RC_NO_USER_KEY, None
    return RC_OK, user_key


def provision_user(root_key, base_url, user_id, role, *, user_key_path=None,
                   cli_config_path=None, timeout=30):
    """创建单个 user 并安全落盘;已有本地凭据时不再次创建。"""
    if not root_key or not user_id or not role or (
        bool(user_key_path) == bool(cli_config_path)
    ):
        print("错误:provision-user 需要 root key、user/role 且恰好一个输出路径", file=sys.stderr)
        return RC_BAD_ENV
    try:
        if user_key_path and os.path.exists(user_key_path):
            with open(user_key_path, encoding="utf-8") as fh:
                if not fh.read().strip():
                    raise ValueError("empty user key")
            return RC_OK
        if cli_config_path:
            cfg = _cli_config_for_output(cli_config_path)
            if cfg.get("api_key"):
                if not (
                    cfg.get("url", "").rstrip("/") == base_url.rstrip("/")
                    and cfg.get("account") == "default"
                    and cfg.get("user") == user_id
                ):
                    raise ValueError("CLI config belongs to another identity")
                return RC_OK
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"错误:本地凭据输出不可安全复用({type(exc).__name__})", file=sys.stderr)
        return RC_MANUAL_RECOVERY

    status, payload = _register_user(base_url, root_key, user_id, role, timeout)
    rc, user_key = _response_user_key(status, payload)
    if rc != RC_OK:
        return rc
    try:
        _write_user_output(
            user_key, base_url, user_id, user_key_path, cli_config_path,
        )
    except (OSError, ValueError) as exc:
        print(
            f"错误:user 已创建但 key 落盘失败({type(exc).__name__});"
            "禁止重复创建,修复输出后使用 rotate-user",
            file=sys.stderr,
        )
        return RC_MANUAL_RECOVERY
    print(f"已创建 user={user_id} 并写入凭据(600)")
    return RC_OK


def rotate_user(root_key, base_url, user_id, *, user_key_path=None,
                cli_config_path=None, timeout=30):
    """轮换单个 user key 并原子更新唯一指定输出。"""
    if not root_key or not user_id or (
        bool(user_key_path) == bool(cli_config_path)
    ):
        print("错误:rotate-user 需要 root key、user 且恰好一个输出路径", file=sys.stderr)
        return RC_BAD_ENV
    try:
        if cli_config_path and os.path.exists(cli_config_path):
            cfg = _cli_config_for_output(cli_config_path)
            configured_url = cfg.get("url")
            if not (
                isinstance(configured_url, str)
                and configured_url.rstrip("/") == base_url.rstrip("/")
                and cfg.get("account") == "default"
                and cfg.get("user") == user_id
            ):
                raise ValueError("CLI config identity does not match rotation target")
    except (OSError, ValueError) as exc:
        print(f"错误:轮换输出配置不可安全复用({type(exc).__name__})", file=sys.stderr)
        return RC_MANUAL_RECOVERY
    status, payload = _rotate_user_key(base_url, root_key, user_id, timeout)
    rc, user_key = _response_user_key(status, payload)
    if rc != RC_OK:
        return rc
    try:
        _write_user_output(
            user_key, base_url, user_id, user_key_path, cli_config_path,
        )
    except (OSError, ValueError) as exc:
        print(
            f"错误:新 key 落盘失败({type(exc).__name__});"
            "禁止创建 user,修复输出后再次轮换",
            file=sys.stderr,
        )
        return RC_MANUAL_RECOVERY
    print(f"已轮换 user={user_id} 并更新凭据(600)")
    return RC_OK


def _server_reachable(base_url, timeout=5):
    """无凭据探测服务是否可达;任何 HTTP 应答(含 401/403)都算可达。"""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/health", method="GET"
    )
    try:
        with _direct_urlopen(req, timeout=timeout) as resp:
            resp.read()
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError):
        return False


def _run_ov(ov_bin, args, cli_config_path, root_key, user_key, env, timeout=90):
    """以密钥经子进程环境的方式调用 ov CLI;返回 CompletedProcess。

    超时不抛异常:折叠为 returncode=124,保持"失败只报步骤与退出码"的契约。
    """
    child_env = dict(env)
    if root_key is None:
        child_env.pop(ROOT_KEY_ENV, None)
    else:
        child_env[ROOT_KEY_ENV] = root_key
    child_env["OPENVIKING_CLI_CONFIG_FILE"] = cli_config_path
    if user_key is not None:
        child_env[USER_KEY_ENV] = user_key
    try:
        return subprocess.run(
            [ov_bin, *args],
            env=child_env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"错误:ov {' '.join(args[:2])} 超时(>{timeout}s)被终止", file=sys.stderr)
        return subprocess.CompletedProcess(args, returncode=124)
    except OSError as exc:
        print(
            f"错误:ov {' '.join(args[:2])} 无法启动({type(exc).__name__})",
            file=sys.stderr,
        )
        return subprocess.CompletedProcess(args, returncode=127)


def _saved_profile_path(cli_config_path, profile_name=CLI_PROFILE_NAME):
    return os.path.join(
        os.path.dirname(cli_config_path), f"ovcli.conf.{profile_name}"
    )


def _load_cli_config(path):
    """读取并解析 CLI 配置;任何读取/解析失败返回 None(配置可能含凭据,勿记录原文)。"""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) else None


def _cfg_matches(cfg, base_url, root_key, user_key, require_root_key=True):
    """对已解析的配置 dict 做一致性判定;None/非 dict 一律不匹配。"""
    if not isinstance(cfg, dict):
        return False
    configured_url = cfg.get("url")
    if configured_url is None and require_root_key:
        # 兼容既有 default CLI 配置;root-free 临时身份配置必须显式含 URL。
        configured_url = DEFAULT_BASE_URL
    root_matches = (
        cfg.get("root_api_key") == root_key
        if require_root_key
        else "root_api_key" not in cfg
    )
    return (
        isinstance(configured_url, str)
        and configured_url.rstrip("/") == base_url.rstrip("/")
        and cfg.get("api_key") == user_key
        and root_matches
    )


def _cli_config_matches(path, base_url, root_key, user_key, require_root_key=True):
    return _cfg_matches(
        _load_cli_config(path), base_url, root_key, user_key, require_root_key,
    )


def _ov_health_ok(ov_bin, cli_config_path, root_key, user_key, env,
                  base_url=DEFAULT_BASE_URL, *, expected_account,
                  expected_user, expected_role, require_root_key,
                  saved_profile_path) -> str:
    """三态判定:"ok" / "invalid"(凭据或配置不符)/ "timeout"(ov 超时)。

    超时(_run_ov 折叠为 returncode 124)不等于凭据无效,调用方必须区别
    处理,不得把有效密钥推向人工轮换(2026-11-03 检视)。
    """
    proc = _run_ov(
        ov_bin, ["health", "-o", "json"], cli_config_path, root_key, user_key, env
    )
    if proc.returncode == 124:
        return "timeout"
    if proc.returncode != 0:
        return "invalid"
    try:
        payload = json.loads(proc.stdout.decode() or "null")
    except ValueError:
        return "invalid"
    if not isinstance(payload, dict):
        return "invalid"
    # 真实 CLI 输出恒为 {"ok":true,"result":{...}};顶层形态无生产者、无测试
    # 覆盖,按 fail-closed 只认文档化信封,不做猜测兼容(2026-11-03 检视)。
    # healthy 只是服务健康,必须同时核对认证身份,避免无 key/错 user 假通过。
    result = payload.get("result")
    if not isinstance(result, dict):
        return "invalid"
    # 仅核对 account/user 不够:ROOT key 也会返回 account_id/user_id=default。
    # 必须排除 role=root,并确认 CLI 配置里存的就是本地 user key(而不是
    # CLI 在 api_key 缺失时回退到 root_api_key)。
    if not (
        result.get("healthy") is True
        and result.get("account_id") == expected_account
        and result.get("user_id") == expected_user
        and result.get("role") == expected_role
        and expected_role != "root"
    ):
        return "invalid"
    if not _cli_config_matches(
        cli_config_path, base_url, root_key, user_key, require_root_key,
    ):
        return "invalid"
    # 是否检查 saved profile 由调用方显式决定;临时配置没有检查对象。
    if saved_profile_path is not None and os.path.exists(saved_profile_path) \
            and not _cli_config_matches(
                saved_profile_path, base_url, root_key, user_key, require_root_key,
            ):
        return "invalid"
    return "ok"


def run(root_key, base_url, cli_config_path, user_key_path, ov_bin="ov",
        env=None, timeout=30):
    """执行初始化;返回进程退出码。所有 print 均不含密钥。"""
    env = dict(os.environ if env is None else env)
    user_key_path = os.path.abspath(user_key_path)
    cli_config_path = os.path.abspath(cli_config_path)

    if not root_key:
        print("错误:环境缺少 OPENVIKING_ROOT_API_KEY(或为空)", file=sys.stderr)
        return RC_BAD_ENV

    user_key_file = os.path.exists(user_key_path)
    cli_cfg_exists = os.path.exists(cli_config_path)

    # ---- 幂等/恢复路径:本地已有 user key,不创建账户 ----
    if user_key_file:
        try:
            with open(user_key_path, encoding="utf-8") as fh:
                stored_key = fh.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"错误:读取 user_api_key 失败({type(exc).__name__}),需人工恢复/轮换",
                file=sys.stderr,
            )
            return RC_MANUAL_RECOVERY
        if not stored_key:
            print("错误:user_api_key 存在但为空,需人工恢复/轮换", file=sys.stderr)
            return RC_MANUAL_RECOVERY
        # config add 分写 saved/active,中断可能只留下一个完整 saved。仅当
        # saved 与本地 key、root 及目标 URL 全部一致时才原子恢复 active;
        # 不一致的 saved 可能是唯一新凭据,绝不能覆盖或拿来强建 active。
        saved_path = _saved_profile_path(cli_config_path)
        # 单次读取即得判决与内容:重复解析会让"读取/解析失败"分支在稳定
        # 文件上不可达(2026-11-03 检视)。
        saved_cfg = _load_cli_config(saved_path)
        if _cfg_matches(saved_cfg, base_url, root_key, stored_key) \
                and not _cli_config_matches(cli_config_path, base_url, root_key, stored_key):
            try:
                _atomic_write_secret(Path(cli_config_path), json.dumps(saved_cfg))
                cli_cfg_exists = True
                print("已从匹配的 saved profile 原子恢复 ovcli.conf")
            except OSError as exc:
                print(
                    f"错误:从 saved profile 恢复 ovcli.conf 失败({type(exc).__name__})",
                    file=sys.stderr,
                )
                return RC_MANUAL_RECOVERY
        if cli_cfg_exists:
            # 部分恢复可能只缺 ovcli.settings.conf;CLI 的显示语言门禁会让
            # `ov health` 在发请求前退出。必须先尽力重建语言设置,否则会把
            # 有效凭据误报为"认证无效"。
            _warn_if_language_setup_failed(_ensure_cli_language(
                ov_bin, {**env, "OPENVIKING_CLI_CONFIG_FILE": cli_config_path}
            ))
            # 先做身份/漂移核查,再做任何写回(含 timeout 修补):漂移的
            # saved profile 必须原样保留现场,不得先追加字段后报错。
            health = _ov_health_ok(
                ov_bin, cli_config_path, root_key, stored_key, env,
                base_url=base_url, expected_account="default",
                expected_user="default", expected_role="admin",
                require_root_key=True,
                saved_profile_path=_saved_profile_path(cli_config_path),
            )
            if health == "timeout":
                print(
                    "错误:ov health 超时被终止;凭据未受质疑,待负载恢复后重跑即可",
                    file=sys.stderr,
                )
                return RC_UPSTREAM
            if health != "ok":
                if not _server_reachable(base_url):
                    print(
                        "错误:服务暂不可达(连接失败),非凭据问题;"
                        "待服务恢复后重跑即可",
                        file=sys.stderr,
                    )
                    return RC_UPSTREAM
                print(
                    "错误:本地凭据存在但认证无效,需人工恢复/轮换(不重复创建账户)",
                    file=sys.stderr,
                )
                return RC_MANUAL_RECOVERY
            if not _ensure_cli_timeout(cli_config_path):
                print("错误:无法确保 ovcli.conf 的 timeout=300", file=sys.stderr)
                return RC_CLI_CONFIG
            print("已初始化:现有认证有效且身份为 default/default,幂等退出")
            return RC_OK
        # 走到这里 cli_cfg_exists 必为 False(上方分支所有路径均已返回)。
        # active 缺失而不一致 saved 仍存在时,先 fail closed;连 timeout 也
        # 不能改写它。
        if os.path.exists(saved_path):
            print(
                "错误:ovcli.conf 缺失且 saved profile 与目标 URL/key 不一致;"
                "已原样保留,需人工恢复/轮换",
                file=sys.stderr,
            )
            return RC_MANUAL_RECOVERY
        # user_key 在而 ovcli.conf 缺失(上次 CLI 配置步骤失败):补建配置。
        # 绝不自动 --force:active 丢失时 saved profile 可能握着更新后的唯一
        # 凭据;若它与本地 key 冲突,必须保留现场并走人工恢复。
        print("检测到 user_api_key 但缺 ovcli.conf,补建 CLI 配置(不覆盖 saved profile)")
        return _configure_cli(
            ov_bin, base_url, cli_config_path, root_key, stored_key, env,
        )

    if cli_cfg_exists:  # 走到这里 user_key_file 必为 False(上方分支均已返回)
        print(
            "错误:ovcli.conf 存在但 user_api_key 缺失,进入人工恢复流程",
            file=sys.stderr,
        )
        return RC_MANUAL_RECOVERY

    # ---- 首次初始化 ----
    status, payload = _post_accounts(base_url, root_key, timeout)
    if status == 409:
        # v0.5.2 服务端首启自动创建无 user 的空壳 default 账户:
        # 回退为在既有账户下注册 admin user(响应同样含 user_key)
        status, payload = _register_default_user(base_url, root_key, timeout)
    if status == 401 or status == 403:
        print("错误:root key 被服务端拒绝(无效或已轮换)", file=sys.stderr)
        return RC_INVALID_ROOT_KEY
    if status == 409 or (
        status == 200 and isinstance(payload, dict) and payload.get("status") != "ok"
    ):
        print(
            "错误:default 账户/user 已存在但本地 user key 缺失,需人工恢复/轮换"
            "(可调 POST /api/v1/admin/accounts/default/users/default/key 轮换)",
            file=sys.stderr,
        )
        return RC_MANUAL_RECOVERY
    if status != 200:
        print(f"错误:账户初始化失败(admin API status={status})", file=sys.stderr)
        return RC_UPSTREAM

    result = (payload or {}).get("result") if isinstance(payload, dict) else None
    user_key = result.get("user_key") if isinstance(result, dict) else None
    if not user_key:
        print("错误:响应缺少 result.user_key,拒绝继续", file=sys.stderr)
        return RC_NO_USER_KEY

    try:
        _atomic_write_secret(Path(user_key_path), user_key)
    except OSError as exc:
        # user 已创建且 key 已从响应取得,此时丢失 key 不得重跑创建流程
        # (会走到 409/人工恢复),必须提示用 Admin API 轮换取新 key。
        print(
            f"错误:user key 落盘失败({type(exc).__name__});default 账户/user 已创建,"
            "禁止重复创建;请用 root key 调 POST "
            "/api/v1/admin/accounts/default/users/default/key 轮换后人工恢复",
            file=sys.stderr,
        )
        return RC_MANUAL_RECOVERY
    print(f"已写入 user_api_key:{user_key_path}(权限 600)")

    return _configure_cli(ov_bin, base_url, cli_config_path, root_key, user_key, env)


def _ensure_cli_language(ov_bin, env):
    """真实 CLI 首跑要求显示语言;尽力设置,返回 CompletedProcess(或 None)。

    env 由调用方保证已含 OPENVIKING_CLI_CONFIG_FILE。已有设置时幂等。
    """
    try:
        return subprocess.run(
            [ov_bin, "language", "zh-CN"],
            env=env,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        print(f"警告:ov language 设置未能执行({type(exc).__name__})", file=sys.stderr)
        return None


def _warn_if_language_setup_failed(proc):
    """语言设置失败不阻断,但必须留下诊断线索:否则 HOME 不可写等环境问题
    会在后续健康检查处被误读为凭据无效(2026-11-03 检视)。"""
    if proc is not None and proc.returncode != 0:
        print(
            f"警告:ov language 设置失败(exit={proc.returncode});"
            "若 CLI 语言门禁未通过,健康检查可能误报凭据无效",
            file=sys.stderr,
        )


def _ensure_timeout_file(path, timeout=300):
    """确保单个 CLI 配置顶层有 "timeout";已设置时不覆盖。

    配置含凭据,必须用唯一临时文件原子替换;任何读取、解析或写入失败都显式
    上报,不能让健康检查掩盖缺失的 timeout。
    """
    name = os.path.basename(path)
    # 与 _load_cli_config 同一解析实现,防止两处读取语义各自漂移
    # (2026-11-03 检视;原先此处另写了一份守卫式 json.load)。
    cfg = _load_cli_config(path)
    if cfg is None:
        print(f"错误:读取/解析 {name} 失败,无法确保 timeout", file=sys.stderr)
        return False
    current = cfg.get("timeout")
    try:
        valid = (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and math.isfinite(current)
            and current >= timeout
        )
    except OverflowError:
        # JSON 可表达任意精度整数;超出 float 范围按"非法值"重写为 300,不崩溃。
        valid = False
    if valid:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            print(
                f"错误:收紧 {name} 权限失败({type(exc).__name__})",
                file=sys.stderr,
            )
            return False
        return True
    # 缺失、非数值或小于 300 的 timeout 都视为不足;写成 300。
    cfg["timeout"] = timeout
    try:
        _atomic_write_secret(Path(path), json.dumps(cfg))
    except OSError as exc:
        print(
            f"错误:原子写入 {name} 失败({type(exc).__name__}),无法确保 timeout",
            file=sys.stderr,
        )
        return False
    return True


def _ensure_cli_timeout(cli_config_path, timeout=300, profile_name=CLI_PROFILE_NAME):
    """确保 active 配置与 CLI saved profile 都有 >= timeout;已足够时不覆盖。

    `ov config add --name local` 会额外写 `ovcli.conf.<name>`(与 active
    配置文件名无关,固定为同目录的 `ovcli.conf.<name>`);只修 active 配置会在
    激活 saved profile 后丢失 timeout。
    """
    if not _ensure_timeout_file(cli_config_path, timeout):
        return False
    saved_path = _saved_profile_path(cli_config_path, profile_name)
    if os.path.exists(saved_path) and not _ensure_timeout_file(saved_path, timeout):
        return False
    return True


def _configure_cli(ov_bin, base_url, cli_config_path, root_key, user_key, env,
                   profile_name=CLI_PROFILE_NAME):
    _warn_if_language_setup_failed(_ensure_cli_language(
        ov_bin, {**env, "OPENVIKING_CLI_CONFIG_FILE": cli_config_path}
    ))
    args = [
        "config", "add", "custom",
        "--name", profile_name,
        "--url", base_url,
        "--activate",
        "-o", "json",
        "--api-key-env", USER_KEY_ENV,
        "--root-api-key-env", ROOT_KEY_ENV,
    ]
    proc = _run_ov(
        ov_bin,
        args,
        cli_config_path,
        root_key,
        user_key,
        env,
    )
    if proc.returncode != 0:
        print(
            f"错误:ov config add 失败(exit={proc.returncode});"
            "user key 与既有 saved profile 均保留;如有同名冲突请人工核对恢复",
            file=sys.stderr,
        )
        return RC_CLI_CONFIG
    try:
        if not os.path.exists(cli_config_path):
            raise FileNotFoundError("ov config add did not create CLI config")
        os.chmod(cli_config_path, 0o600)
    except OSError as exc:
        print(
            f"错误:ovcli.conf 缺失或无法收紧权限({type(exc).__name__})",
            file=sys.stderr,
        )
        return RC_CLI_CONFIG
    if not _ensure_cli_timeout(cli_config_path, profile_name=profile_name):
        return RC_CLI_CONFIG

    health = _ov_health_ok(
        ov_bin, cli_config_path, root_key, user_key, env,
        base_url=base_url, expected_account="default", expected_user="default",
        expected_role="admin", require_root_key=True,
        saved_profile_path=_saved_profile_path(cli_config_path, profile_name),
    )
    if health == "timeout":
        print(
            "错误:ov health 验证超时;user key 与配置已写入,待服务恢复后重跑幂等验证",
            file=sys.stderr,
        )
        return RC_UPSTREAM
    if health != "ok":
        print("错误:ov health 验证未通过(healthy/account/user 不符合预期)", file=sys.stderr)
        return RC_AUTH_VERIFY
    print("认证初始化完成:ovcli.conf(600)写入,default/default healthy=true")
    return RC_OK


def check_user(ov_bin, base_url, cli_config_path, user_key_path, env=None, *,
               account_id="default", user_id, role="user"):
    """用只含 USER key 的临时配置验证身份,并尽力保证退出后无副本。"""
    if not base_url or not user_id or role == "root":
        return RC_BAD_ENV
    if os.path.abspath(cli_config_path) == os.path.abspath(user_key_path):
        print("错误:临时配置路径不得与 key 文件相同", file=sys.stderr)
        return RC_MANUAL_RECOVERY
    if os.path.lexists(cli_config_path):
        print("错误:临时配置路径已存在;拒绝覆盖或删除,请先人工核对", file=sys.stderr)
        return RC_MANUAL_RECOVERY
    env = dict(os.environ if env is None else env)
    env.pop(ROOT_KEY_ENV, None)
    try:
        with open(user_key_path, encoding="utf-8") as fh:
            user_key = fh.read().strip()
        if not user_key:
            raise ValueError("empty user key")
        cfg = {
            "url": base_url, "api_key": user_key,
            "account": account_id, "user": user_id,
        }
        _exclusive_write_secret(Path(cli_config_path), json.dumps(cfg))
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"错误:无法准备临时身份配置({type(exc).__name__})", file=sys.stderr)
        return RC_MANUAL_RECOVERY

    rc = RC_OK
    try:
        child_env = {**env, "OPENVIKING_CLI_CONFIG_FILE": cli_config_path}
        _warn_if_language_setup_failed(_ensure_cli_language(ov_bin, child_env))
        health = _ov_health_ok(
            ov_bin, cli_config_path, None, user_key, env,
            base_url=base_url, expected_account=account_id,
            expected_user=user_id, expected_role=role, require_root_key=False,
            saved_profile_path=None,
        )
        if health == "timeout":
            rc = RC_UPSTREAM
        elif health != "ok":
            rc = RC_AUTH_VERIFY
    finally:
        try:
            if os.path.lexists(cli_config_path):
                os.unlink(cli_config_path)
        except OSError as exc:
            print(f"错误:临时身份配置删除失败({type(exc).__name__})", file=sys.stderr)
            rc = RC_MANUAL_RECOVERY
    if rc == RC_OK:
        print(f"身份验证通过:{account_id}/{user_id}/{role};临时配置已删除")
    else:
        print(f"错误:身份验证未通过(user={user_id}, rc={rc})", file=sys.stderr)
    return rc


def main():
    parser = argparse.ArgumentParser(description="OpenViking 用户认证初始化与检查")
    sub = parser.add_subparsers(dest="command")
    provision = sub.add_parser("provision-user")
    provision.add_argument("--user-id", required=True)
    provision.add_argument("--role", required=True)
    output = provision.add_mutually_exclusive_group(required=True)
    output.add_argument("--key-file")
    output.add_argument("--cli-config")
    rotate = sub.add_parser("rotate-user")
    rotate.add_argument("--user-id", required=True)
    output = rotate.add_mutually_exclusive_group(required=True)
    output.add_argument("--key-file")
    output.add_argument("--cli-config")
    check = sub.add_parser("check-user")
    check.add_argument("--user-id", required=True)
    check.add_argument("--role", default="user")
    check.add_argument("--account", default="default")
    check.add_argument("--key-file", required=True)
    check.add_argument("--config-file")
    args = parser.parse_args()

    root_key = os.environ.get(ROOT_KEY_ENV, "")
    base_url = os.environ.get("OPENVIKING_BOOTSTRAP_URL", DEFAULT_BASE_URL)
    cli_config_path = os.environ.get(
        "OPENVIKING_CLI_CONFIG_FILE", "/app/.openviking/ovcli.conf"
    )
    default_user_key = os.path.join(
        os.path.dirname(cli_config_path), "secrets", "user_api_key"
    )
    user_key_path = os.environ.get("OPENVIKING_USER_KEY_FILE", default_user_key)
    ov_bin = os.environ.get("OV_BIN", "ov")

    if args.command == "provision-user":
        rc = provision_user(
            root_key, base_url, args.user_id, args.role,
            user_key_path=args.key_file, cli_config_path=args.cli_config,
        )
    elif args.command == "rotate-user":
        rc = rotate_user(
            root_key, base_url, args.user_id,
            user_key_path=args.key_file, cli_config_path=args.cli_config,
        )
    elif args.command == "check-user":
        rc = check_user(
            ov_bin, base_url,
            args.config_file or f"/tmp/ovcli.{args.user_id}.conf",
            args.key_file, account_id=args.account,
            user_id=args.user_id, role=args.role,
        )
    else:
        rc = run(root_key, base_url, cli_config_path, user_key_path, ov_bin=ov_bin)
    sys.exit(rc)


if __name__ == "__main__":
    main()
