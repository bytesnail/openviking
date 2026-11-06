#!/usr/bin/env python3
"""secrets.env / ov.conf 的只读解析与校验(prepare.sh 与 e2e_auth_matrix.sh 共用)。

解析规则覆盖 Docker Compose env_file 的常用形态:
- UTF-8 BOM、CRLF、缩进行、`export KEY=...`、`KEY = value`、最后一条赋值生效;
- 双/单引号、行内注释(` #` 起;`VALUE#literal` 与紧跟 `=` 的 `#literal` 保留;
  `KEY=   # comment` 的注释不算值,解析为空);
- 不实现 `$`/`${}` 插值与双引号转义(本项目密钥为 hex,不含这些字符;
  双引号值内出现反斜杠转义时直接报错,防止"校验通过但 Compose 注入不同值";
  值含 `$` 时同样直接报错——Compose 对 env_file 值做 $VAR/$$ 插值,引号内
  不豁免,字面读取会与容器注入值静默不一致)。

用法:
  env_file.py get <secrets.env> <NAME>          输出 NAME 的值(调用方捕获,不打印)
  env_file.py precheck <secrets.env>            检查 Z_AI/root key;输出 GENERATE|KEEP
  env_file.py validate <secrets.env> <ov.conf>  校验展开后的配置并检查模型名漂移

所有错误只输出步骤/异常类型,绝不回显密钥值。
"""
import json
import posixpath
import re
import sys
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向。

    urllib 默认对 301/302/303 的 POST 会转 GET 并原样携带 Authorization 等
    header 追到 Location 目标(实测确认),本地管理/校验请求绝不允许被重定向
    带走。返回 None 使 opener 以 HTTPError(3xx) 报错,不发送后续请求。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect()
)
# 同型 _NoRedirect/_DIRECT_OPENER 在 scripts/bootstrap_auth.py 与
# scripts/embedding_adapter.py 各有一份:保持 -I 下零 sys.path 操作、脚本
# 自包含是有意设计(显式 sys.path.insert 本可共享,先例
# tests/e2e_adapter_probe.py)。共享化已定为触发式(2026-11-07 用户决策):
# 下次 opener/传输层加固时建 scripts/_direct_http.py 单一来源,触发器→
# 范围映射见项目卡"统一视角"。本模块是探针(e2e_adapter_probe.py)的
# 复用来源;在此之前,对任何一份的加固必须同步其余两份,
# tests/test_env_file.py 有行为一致性断言。

# 变量引用语法只此一份:展开与"未展开残留"检测共用,防止两份字面量漂移。
_VAR_PATTERN = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)


class EnvFileError(Exception):
    """可安全展示给用户的错误(不含密钥值)。"""


def parse_env_value(value):
    parsed = None
    quoted = (
        re.fullmatch(r'\s*("(?:[^"\\]|\\.)*")\s*(?:#.*)?', value)
        or re.fullmatch(r"\s*('[^']*')\s*(?:#.*)?", value)
    )
    if quoted:
        token = quoted.group(1)
        if token.startswith('"') and "\\" in token:
            # 不实现双引号转义;compose 会按转义解码而本解析器保留原文,
            # 两侧值会静默不一致,故 fail closed。本项目密钥为 hex,不含反斜杠。
            raise EnvFileError(
                "双引号值内含反斜杠转义,本解析器不支持;请改用不含转义的值"
            )
        parsed = token[1:-1]
    elif value.lstrip().startswith(('"', "'")):
        # 引号开头却未整段匹配成合法引号值 = 引号未闭合,或转义吃掉了收尾
        # 引号;原样走非引号路径放行会造成"本解析器值 ≠ Compose 注入值"
        # 的静默分歧,必须显式报错(2026-11-03 检视)。
        raise EnvFileError(
            "引号值未闭合或含不支持的转义(双引号内不支持反斜杠转义)"
        )
    else:
        # Compose:非引号值的行内注释以"空白 + #"开始,VALUE#literal 不算。
        # 必须在未 strip 的原文上切,保留"# 前是否有空白"的信息:先 strip 会把
        # `KEY=   # note` 变成值 "# note",而 compose 解析为空值。
        parsed = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    # '$' 一律拒绝(实测 Compose v5.6.1 对 env_file 值做 $VAR/$$ 插值,
    # 单双引号内都不豁免):本解析器按字面读取,两侧值会静默不一致——
    # 校验通过而容器注入不同值,正是反斜杠守卫要堵的同一类分歧。
    if "$" in parsed:
        raise EnvFileError(
            "值含 '$':Compose 会对 $VAR/$$ 做插值而本解析器按字面读取,"
            "两侧值会静默不一致;请改用不含 '$' 的值"
        )
    return parsed


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
        if line.startswith("export"):
            rest = line[len("export"):]
            if rest[:1] == " ":
                line = rest.lstrip()
            elif rest[:1] in "\t\r\v\f":
                # export+tab 会被当成名为 "export\tKEY" 的变量,触发误导性的
                # "缺失"报错;直接明确拒绝,不猜 compose 是否接受该变体
                # (2026-11-03 检视)。
                raise EnvFileError("export 前缀后必须是空格(不支持 tab 等其他空白)")
            # 其余情况(如 exports=1)按普通变量名处理
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        env[key] = parse_env_value(value)
    return env


# 常见占位符黑名单;词条长度均 < 16,与下方长度下界构成两道独立防线
# (黑名单为未来更长词条预留;tests/test_env_file.py 以注入方式覆盖该分支)。
PLACEHOLDER_ROOT_KEYS = {
    "changeme", "change-me", "changethis", "password", "secret",
    "root", "test", "example", "placeholder", "dummy",
}


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
    if root.lower() in PLACEHOLDER_ROOT_KEYS:
        raise EnvFileError("OPENVIKING_ROOT_API_KEY 是常见占位符,视为未配置")
    if len(root) < 16:
        raise EnvFileError("OPENVIKING_ROOT_API_KEY 过短(至少 16 字符)")
    return "KEEP"


def _expand_config_vars(raw, env):
    """只用 Compose env_file 提供的值展开,不读取或修改宿主 os.environ。"""
    def replace(match):
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))

    return _VAR_PATTERN.sub(replace, raw)


def validate_config(env, config_path):
    """展开并校验 ov.conf;返回 cfg;失败抛 EnvFileError。"""
    try:
        raw = open(config_path, encoding="utf-8-sig").read()
    except (OSError, UnicodeDecodeError) as exc:
        raise EnvFileError(f"读取 ov.conf 失败({type(exc).__name__})") from None
    expanded = _expand_config_vars(raw, env)
    leftover = sorted(set(
        name or short for name, short in _VAR_PATTERN.findall(expanded)
    ))
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
    zai_key = env.get("Z_AI_API_KEY")
    root_key = env.get("OPENVIKING_ROOT_API_KEY")
    if not zai_key or not root_key:
        raise EnvFileError("secrets.env 缺少 Z_AI_API_KEY 或 OPENVIKING_ROOT_API_KEY")
    if vlm.get("api_key") != zai_key or server.get("root_api_key") != root_key:
        raise EnvFileError("密钥字段与 secrets.env 值不一致")
    return cfg


def loaded_model_ids(data):
    """从 /v1/models 提取规范化精确 ID,供 prepare 与 e2e 共用。"""
    if not isinstance(data, dict):
        return set()
    items = data.get("data") or data.get("models") or []
    if not isinstance(items, list):
        return set()
    return {
        str(item.get("id") or item.get("name") or "").strip().lower()
        for item in items
        if isinstance(item, dict) and (item.get("id") or item.get("name"))
    }


def model_name_matches(model, ids):
    """配置模型名与加载列表是否指同一模型(容忍 id 形态漂移)。

    llama-server 的 id 形态随版本漂移:早期为裸名(embeddinggemma-2),
    2026-11-02 实测两端口均为完整路径(/mnt/.../embeddinggemma-2-F16.gguf)。
    判定:id、basename 或去扩展名 stem 恰等于配置名,或以"配置名-"开头
    (量化/精度后缀变体);完全无关的名字仍判不匹配,保留漂移预警意义。
    """
    if not isinstance(model, str) or not model.strip():
        return False
    target = model.strip().lower()
    for item in ids:
        if not isinstance(item, str):
            continue
        clean = item.strip().lower()
        base = posixpath.basename(clean)
        stem = base.rsplit(".", 1)[0] if "." in base else base
        if clean == target or base == target or stem == target \
                or stem.startswith(target + "-"):
            return True
    return False


def check_model_drift(cfg):
    """模型名漂移预警(只警告,不阻断)。"""
    embedding = cfg.get("embedding")
    dense = embedding.get("dense") if isinstance(embedding, dict) else None
    rerank = cfg.get("rerank")
    # 探测目标为 model-serving 直连端口(有意绕过适配器);拓扑权威表见 README。
    checks = [
        ("embedding", "18351", dense.get("model", "") if isinstance(dense, dict) else ""),
        ("rerank", "18352", rerank.get("model", "") if isinstance(rerank, dict) else ""),
    ]
    for label, port, model in checks:
        if not isinstance(model, str) or not model:
            continue
        try:
            with _DIRECT_OPENER.open(
                f"http://127.0.0.1:{port}/v1/models", timeout=3
            ) as response:
                data = json.load(response)
            ids = loaded_model_ids(data)
            if model_name_matches(model, ids):
                continue
            detail = " ".join(sorted(ids)) or "(加载列表为空)"
        except Exception as exc:
            detail = f"查询失败({type(exc).__name__}),跳过核对"
        print(
            f"PREPARE_WARN: {label} 上游({port})/v1/models 未匹配到 {model!r}"
            f"(含路径/量化后缀形态)({detail});模型名可能漂移,请人工确认",
            file=sys.stderr,
        )


def main(argv):
    # 子命令一律严格等元数:get 多带参数(误写成 validate 形态)会被静默
    # 忽略并照常输出密钥值,必须与 precheck/validate 同纪律直接拒绝
    # (2026-11-03 检视)。
    if len(argv) == 4 and argv[1] == "get":
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
