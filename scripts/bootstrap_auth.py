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
import http.client
import json
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


def _atomic_write_secret(path, content):
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


def _post_json(url, body, root_key, timeout=30):
    """POST JSON with root key; return (status:int, payload:dict|None); status=0 on connection failure."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {root_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    except (urllib.error.URLError, http.client.HTTPException, OSError):
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


def _register_default_user(base_url, root_key, timeout=30):
    """Register an admin user under the auto-created default account on v0.5.2 first startup (accounts endpoint returns 409)."""
    return _post_json(
        base_url.rstrip("/") + "/api/v1/admin/accounts/default/users",
        {"user_id": "default", "role": "admin"},
        root_key,
        timeout,
    )


def _run_ov(ov_bin, args, cli_config_path, root_key, user_key, env, timeout=90):
    """以密钥经子进程环境的方式调用 ov CLI;返回 CompletedProcess。

    超时不抛异常:折叠为 returncode=124,保持"失败只报步骤与退出码"的契约。
    """
    child_env = dict(env)
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


def _ov_health_ok(ov_bin, cli_config_path, root_key, user_key, env) -> bool:
    proc = _run_ov(
        ov_bin, ["health", "-o", "json"], cli_config_path, root_key, user_key, env
    )
    if proc.returncode != 0:
        return False
    try:
        payload = json.loads(proc.stdout.decode() or "null")
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    # 真实 CLI 输出为 {"ok":true,"result":{"healthy":true,...}};兼容顶层形态。
    # healthy 只是服务健康,必须同时核对认证身份,避免无 key/错 user 假通过。
    result = payload.get("result")
    healthy = payload.get("healthy")
    identity = payload
    if isinstance(result, dict):
        healthy = result.get("healthy", healthy)
        identity = result
    return (
        healthy is True
        and identity.get("account_id") == "default"
        and identity.get("user_id") == "default"
    )


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
        if cli_cfg_exists:
            if not _ensure_cli_timeout(cli_config_path):
                print("错误:无法确保 ovcli.conf 的 timeout=300", file=sys.stderr)
                return RC_CLI_CONFIG
            if _ov_health_ok(ov_bin, cli_config_path, root_key, stored_key, env):
                print("已初始化:现有认证有效且身份为 default/default,幂等退出")
                return RC_OK
            print(
                "错误:本地凭据存在但认证无效,需人工恢复/轮换(不重复创建账户)",
                file=sys.stderr,
            )
            return RC_MANUAL_RECOVERY
        # user_key 在而 ovcli.conf 缺失(上次 CLI 配置步骤失败):补建配置
        print("检测到 user_api_key 但缺 ovcli.conf,补建 CLI 配置")
        return _configure_cli(
            ov_bin, base_url, cli_config_path, root_key, stored_key, env
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
    """真实 CLI 首跑要求显示语言;尽力设置,失败不阻断(已有设置时幂等)。

    env 由调用方保证已含 OPENVIKING_CLI_CONFIG_FILE。
    """
    try:
        subprocess.run(
            [ov_bin, "language", "zh-CN"],
            env=env,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:
        pass


def _ensure_cli_timeout(cli_config_path, timeout=300):
    """确保 ovcli.conf 顶层有 "timeout";已设置时不覆盖。

    返回是否已确保。配置含凭据,必须用唯一临时文件原子替换;任何读取、
    解析或写入失败都显式上报,不能让健康检查掩盖缺失的 timeout。
    """
    try:
        with open(cli_config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        print(
            f"错误:读取/解析 ovcli.conf 失败({type(exc).__name__}),无法确保 timeout",
            file=sys.stderr,
        )
        return False
    if not isinstance(cfg, dict):
        print("错误:ovcli.conf 顶层不是对象,无法确保 timeout", file=sys.stderr)
        return False
    if "timeout" in cfg:
        return True
    cfg["timeout"] = timeout
    try:
        _atomic_write_secret(Path(cli_config_path), json.dumps(cfg))
    except OSError as exc:
        print(
            f"错误:原子写入 ovcli.conf 失败({type(exc).__name__}),无法确保 timeout",
            file=sys.stderr,
        )
        return False
    return True


def _configure_cli(ov_bin, base_url, cli_config_path, root_key, user_key, env):
    _ensure_cli_language(ov_bin, {**env, "OPENVIKING_CLI_CONFIG_FILE": cli_config_path})
    proc = _run_ov(
        ov_bin,
        [
            "config", "add", "custom",
            "--name", "local",
            "--url", base_url,
            "--activate",
            "-o", "json",
            "--api-key-env", USER_KEY_ENV,
            "--root-api-key-env", ROOT_KEY_ENV,
        ],
        cli_config_path,
        root_key,
        user_key,
        env,
    )
    if proc.returncode != 0:
        print(
            f"错误:ov config add 失败(exit={proc.returncode});"
            "user key 已保留,修复后重跑本脚本可自动补建配置",
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
    if not _ensure_cli_timeout(cli_config_path):
        return RC_CLI_CONFIG

    if not _ov_health_ok(ov_bin, cli_config_path, root_key, user_key, env):
        print("错误:ov health 验证未通过(healthy/account/user 不符合预期)", file=sys.stderr)
        return RC_AUTH_VERIFY
    print("认证初始化完成:ovcli.conf(600)写入,default/default healthy=true")
    return RC_OK


def main():
    root_key = os.environ.get(ROOT_KEY_ENV, "")
    base_url = os.environ.get("OPENVIKING_BOOTSTRAP_URL", "http://127.0.0.1:1933")
    cli_config_path = os.environ.get(
        "OPENVIKING_CLI_CONFIG_FILE", "/app/.openviking/ovcli.conf"
    )
    default_user_key = os.path.join(
        os.path.dirname(cli_config_path), "secrets", "user_api_key"
    )
    user_key_path = os.environ.get("OPENVIKING_USER_KEY_FILE", default_user_key)
    ov_bin = os.environ.get("OV_BIN", "ov")
    sys.exit(
        run(root_key, base_url, cli_config_path, user_key_path, ov_bin=ov_bin)
    )


if __name__ == "__main__":
    main()
