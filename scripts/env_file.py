#!/usr/bin/env python3
"""secrets.env / ov.conf 的只读解析与校验(prepare.sh 与 e2e_auth_matrix.sh 共用)。

解析规则覆盖 Docker Compose env_file 的常用形态:
- UTF-8 BOM、CRLF、缩进行、`export KEY=...`、`KEY = value`、最后一条赋值生效;
- 双/单引号、行内注释(` #` 起;`VALUE#literal` 保留);
- 不实现 `$`/`${}` 插值与双引号转义(本项目密钥为 hex,不含这些字符)。

用法:
  env_file.py get <secrets.env> <NAME>          输出 NAME 的值(调用方捕获,不打印)
  env_file.py precheck <secrets.env>            检查 Z_AI/root key;输出 GENERATE|KEEP
  env_file.py validate <secrets.env> <ov.conf>  校验展开后的配置并检查模型名漂移

所有错误只输出步骤/异常类型,绝不回显密钥值。
"""
import json
import os
import re
import sys
import urllib.request


class EnvFileError(Exception):
    """可安全展示给用户的错误(不含密钥值)。"""


def parse_env_value(value):
    value = value.strip()
    quoted = (
        re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*(?:#.*)?', value)
        or re.fullmatch(r"('[^']*')\s*(?:#.*)?", value)
    )
    if quoted:
        # 不实现双引号转义;本项目密钥为 hex,不含引号/反斜杠。
        return quoted.group(1)[1:-1]
    # Compose:非引号值的空白 + # 开始行内注释,VALUE#literal 不算。
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def parse_env_file(path):
    """按 Compose 常用语义解析 env_file;重复 NAME 以最后一次为准。"""
    try:
        # secrets.env 可能含非 UTF-8 注释;替换而非崩溃。
        text = open(path, encoding="utf-8-sig", errors="replace").read()
    except OSError as exc:
        raise EnvFileError(f"读取 {path} 失败({type(exc).__name__})") from None
    env = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        env[key] = parse_env_value(value)
    return env


def precheck_env(env):
    """返回 GENERATE(缺 root key,需生成)或 KEEP;失败抛 EnvFileError。"""
    zai = env.get("Z_AI_API_KEY", "")
    if not zai.strip():
        raise EnvFileError("Z_AI_API_KEY 缺失或为空(含成对引号的空值)")
    if not zai.strip("xX"):
        raise EnvFileError("Z_AI_API_KEY 是纯 X 占位符,视为未配置")
    if "OPENVIKING_ROOT_API_KEY" not in env:
        return "GENERATE"
    root = env["OPENVIKING_ROOT_API_KEY"].strip()
    if not root:
        raise EnvFileError(
            "OPENVIKING_ROOT_API_KEY 已存在但为空(含成对引号的空值);"
            "按设计不覆盖,请人工轮换后再执行"
        )
    if not root.strip("xX"):
        raise EnvFileError("OPENVIKING_ROOT_API_KEY 是纯 X 占位符,视为未配置")
    if root.lower() in {
        "changeme", "change-me", "changethis", "password", "secret",
        "root", "test", "example", "placeholder", "dummy",
    }:
        raise EnvFileError("OPENVIKING_ROOT_API_KEY 是常见占位符,视为未配置")
    if len(root) < 16:
        raise EnvFileError("OPENVIKING_ROOT_API_KEY 过短(至少 16 字符)")
    return "KEEP"


def validate_config(env, config_path):
    """展开并校验 ov.conf;返回 cfg;失败抛 EnvFileError。"""
    os.environ.update(env)
    try:
        raw = open(config_path, encoding="utf-8-sig").read()
    except (OSError, UnicodeDecodeError) as exc:
        raise EnvFileError(f"读取 ov.conf 失败({type(exc).__name__})") from None
    expanded = os.path.expandvars(raw)
    leftover = sorted(set(re.findall(r"\$\{([^}]+)\}", expanded)))
    if leftover:
        raise EnvFileError(f"配置含未展开的变量引用: {', '.join(leftover)}")
    try:
        cfg = json.loads(expanded)
    except ValueError:
        raise EnvFileError("展开后的 ov.conf 不是合法 JSON") from None
    if not isinstance(cfg, dict):
        raise EnvFileError("展开后的 ov.conf 顶层不是 JSON 对象")
    vlm = cfg.get("vlm")
    server = cfg.get("server")
    if not isinstance(vlm, dict) or not isinstance(server, dict):
        raise EnvFileError("ov.conf 缺少 vlm/server 对象")
    if (
        vlm.get("api_key") != env["Z_AI_API_KEY"]
        or server.get("root_api_key") != env["OPENVIKING_ROOT_API_KEY"]
    ):
        raise EnvFileError("密钥字段与 secrets.env 值不一致")
    return cfg


def check_model_drift(cfg):
    """模型名漂移预警(只警告,不阻断)。"""
    embedding = cfg.get("embedding")
    dense = embedding.get("dense") if isinstance(embedding, dict) else None
    rerank = cfg.get("rerank")
    checks = [
        ("embedding", "18351", dense.get("model", "") if isinstance(dense, dict) else ""),
        ("rerank", "18352", rerank.get("model", "") if isinstance(rerank, dict) else ""),
    ]
    for label, port, model in checks:
        if not isinstance(model, str) or not model:
            continue
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=3
            ) as response:
                data = json.load(response)
            items = data.get("data") or data.get("models") or []
            ids = " ".join(
                str(item.get("id") or item.get("name") or "")
                for item in items
                if isinstance(item, dict)
            ).lower()
            if model.lower() in ids:
                continue
            detail = ids or "(加载列表为空)"
        except Exception as exc:
            detail = f"查询失败({type(exc).__name__}),跳过核对"
        print(
            f"PREPARE_WARN: {label} 上游({port})/v1/models 未含 {model!r}({detail});"
            "模型名可能漂移,请人工确认",
            file=sys.stderr,
        )


def main(argv):
    if len(argv) >= 4 and argv[1] == "get":
        try:
            env = parse_env_file(argv[2])
        except EnvFileError as exc:
            print(f"PREPARE_FAIL: {exc}", file=sys.stderr)
            return 1
        sys.stdout.write(env.get(argv[3], ""))
        return 0
    if len(argv) == 3 and argv[1] == "precheck":
        try:
            env = parse_env_file(argv[2])
            action = precheck_env(env)
        except EnvFileError as exc:
            print(f"PREPARE_FAIL: {exc}", file=sys.stderr)
            return 1
        print(action)
        return 0
    if len(argv) == 4 and argv[1] == "validate":
        try:
            env = parse_env_file(argv[2])
            cfg = validate_config(env, argv[3])
        except EnvFileError as exc:
            print(f"PREPARE_FAIL: {exc}", file=sys.stderr)
            return 1
        print("ov.conf 展开+JSON 校验通过")
        check_model_drift(cfg)
        return 0
    print(
        "用法: env_file.py get <secrets.env> <NAME> | "
        "precheck <secrets.env> | validate <secrets.env> <ov.conf>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
