#!/usr/bin/env bash
# OpenViking Compose 部署前置准备(设计 §7.1、§5 约束)。
#
# 纪律:
# - 绝不 source secrets.env,绝不回显任何变量值;
# - root key 缺失则生成,存在但为空则失败,重复执行幂等;
# - state 含未知数据时停止,不清空、不接管;
# - 端口 1933/18353 必须空闲,18351/18352 必须已由 model-serving 提供。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_FILE="$BASE_DIR/secrets.env"
CONFIG_FILE="$BASE_DIR/config/ov.conf"
COMPOSE_FILE="$BASE_DIR/compose.yaml"
STATE_DIR="$BASE_DIR/state"
SECRETS_DIR="$STATE_DIR/secrets"
BACKUP_DIR="$BASE_DIR/backups"
MARKER="$STATE_DIR/.openviking-compose-v1"
# Compose 中 openviking service 的 user(设计 §5:主服务以宿主 meteor 的 1000:1000 运行)
EXPECTED_UID_GID="1000:1000"

fail() { echo "prepare 失败:$*" >&2; exit 1; }

# ---------- 配置文件形态(防止 Compose 把缺失挂载源创建成目录) ----------
[ -f "$COMPOSE_FILE" ] || fail "缺少 $COMPOSE_FILE"
[ -f "$CONFIG_FILE" ] || fail "缺少 $CONFIG_FILE(必须是常规文件,不能是目录)"
[ -f "$SECRETS_FILE" ] || fail "缺少 $SECRETS_FILE"

# ---------- 宿主 UID/GID 与 Compose 一致性 ----------
current_uid_gid="$(id -u):$(id -g)"
[ "$current_uid_gid" = "$EXPECTED_UID_GID" ] \
  || fail "当前用户 $current_uid_gid 与 Compose user $EXPECTED_UID_GID 不一致;其他用户部署须先显式调整 compose.yaml 与本脚本常量"

# ---------- 端口 ----------
port_listening() { ss -H -tln "sport = :$1" 2>/dev/null | grep -q .; }
for p in 1933 18353; do
  if port_listening "$p"; then
    fail "端口 $p 已被占用(应为 OpenViking 部署保留);不杀既有进程,请先排查"
  fi
done
for p in 18351 18352; do
  if ! port_listening "$p"; then
    fail "端口 $p 未监听:model-serving 未提供(embedding=18351 / rerank=18352);本方案不修改 model-serving"
  fi
done

# ---------- secrets.env 解析(只解析,不执行) ----------
read_var() { # read_var NAME -> 输出值(调用方捕获,不回显);变量不存在时输出空串
  # 与 docker compose env_file 同语义:去首尾空白、剥成对引号、去行内注释
  # (实测 compose 对 KEY="v" # comment 与 KEY=v # comment 均注入 v)
  local raw double_quoted single_quoted
  raw="$(grep -E "^${1}=" "$SECRETS_FILE" 2>/dev/null | tail -n 1 | cut -d= -f 2- || true)"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  double_quoted='^("([^"\\]|\\.)*")[[:space:]]*(#.*)?$'
  single_quoted="^('[^']*')[[:space:]]*(#.*)?$"
  if [[ "$raw" =~ $double_quoted ]] || [[ "$raw" =~ $single_quoted ]]; then
    raw="${BASH_REMATCH[1]}"
    raw="${raw:1:${#raw}-2}"
  else
    # 非引号值中,空白后的 # 是注释;VALUE#literal 仍保留 #。
    raw="${raw%%[[:space:]]#*}"
    raw="${raw%"${raw##*[![:space:]]}"}"
  fi
  case "$raw" in
    *[![:space:]]*) ;;  # 含非空白字符,保留原样(引号内的合法内部空白不动)
    *) raw="" ;;        # 剥引号后纯空白 → 视为空
  esac
  printf '%s' "$raw"
}

zai_key="$(read_var Z_AI_API_KEY)"
[ -n "$zai_key" ] || fail "secrets.env 缺少 Z_AI_API_KEY 或值为空(含成对引号的空值)"
# 纯 X 占位符(如 xxxxx / XXXXX)视为未配置
if [ -z "${zai_key//[xX]/}" ]; then
  fail "Z_AI_API_KEY 是纯 X 占位符,视为未配置"
fi

if grep -qE '^OPENVIKING_ROOT_API_KEY=' "$SECRETS_FILE"; then
  root_key="$(read_var OPENVIKING_ROOT_API_KEY)"
  [ -n "$root_key" ] || fail "OPENVIKING_ROOT_API_KEY 已存在但为空(含成对引号的空值);按设计不覆盖,请人工轮换后再执行"
  root_key_status="已存在,保持不变"
else
  # 密码学安全随机 32 字节 hex;直接追加,不动已有内容
  new_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  last_char="$(tail -c 1 "$SECRETS_FILE")"
  if [ -n "$last_char" ]; then
    printf '\n' >> "$SECRETS_FILE"
  fi
  printf 'OPENVIKING_ROOT_API_KEY=%s\n' "$new_key" >> "$SECRETS_FILE"
  root_key_status="已生成(32 字节 hex)"
fi
chmod 600 "$SECRETS_FILE"

# ---------- 展开后配置可解析且密钥位无未展开字面值 ----------
python3 - "$SECRETS_FILE" "$CONFIG_FILE" <<'PY'
import json, os, re, sys

secrets_path, config_path = sys.argv[1], sys.argv[2]
env = {}

def parse_env_value(value):
    value = value.strip()
    quoted = (
        re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*(?:#.*)?', value)
        or re.fullmatch(r"('[^']*')\s*(?:#.*)?", value)
    )
    if quoted:
        return quoted.group(1)[1:-1]
    # Compose:非引号值的空白 + # 开始行内注释,VALUE#literal 不算。
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()

for line in open(secrets_path, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    env[key.strip()] = parse_env_value(value)

for name in ("Z_AI_API_KEY", "OPENVIKING_ROOT_API_KEY"):
    if not env.get(name, "").strip():
        print(f"PREPARE_FAIL: {name} 缺失或为空")
        sys.exit(1)

os.environ.update(env)
raw = open(config_path, encoding="utf-8-sig").read()
expanded = os.path.expandvars(raw)
leftover = sorted(set(re.findall(r"\$\{([^}]+)\}", expanded)))
if leftover:
    print(f"PREPARE_FAIL: 配置含未展开的变量引用: {', '.join(leftover)}")
    sys.exit(1)
try:
    cfg = json.loads(expanded)
except ValueError:
    print("PREPARE_FAIL: 展开后的 ov.conf 不是合法 JSON")
    sys.exit(1)
ok = (
    cfg["vlm"]["api_key"] == env["Z_AI_API_KEY"]
    and cfg["server"]["root_api_key"] == env["OPENVIKING_ROOT_API_KEY"]
)
if not ok:
    print("PREPARE_FAIL: 密钥字段与 secrets.env 值不一致")
    sys.exit(1)
print("ov.conf 展开+JSON 校验通过")
PY

# ---------- 模型名漂移预警(仅警告,不阻断) ----------
# llama-server 不校验请求里的 model 字段(按已加载模型服务),ov.conf 的模型名
# 与实际加载模型漂移时,smoke/doctor 等功能性检查全部无感——2026-11-01 reranker
# 换型即靠人工发现。此处对 18351/18352 各取 /v1/models,按大小写不敏感子串比对
# (服务端报告的是 gguf 路径,含配置名即视为匹配)。端口可达性已由上文检查。
python3 - "$CONFIG_FILE" "$SECRETS_FILE" <<'PY'
import json, os, re, sys, urllib.request

def parse_env_value(value):
    value = value.strip()
    quoted = (
        re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*(?:#.*)?', value)
        or re.fullmatch(r"('[^']*')\s*(?:#.*)?", value)
    )
    if quoted:
        return quoted.group(1)[1:-1]
    # Compose:非引号值的空白 + # 开始行内注释,VALUE#literal 不算。
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()

env = {}
for line in open(sys.argv[2], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    env[key.strip()] = parse_env_value(value)
os.environ.update(env)

try:
    # 与上文校验 heredoc 同语义:先加载 secrets.env 再 expandvars,避免
    # ${VAR} 字面值被当成模型名参与漂移比对
    raw = open(sys.argv[1], encoding="utf-8-sig").read()
    cfg = json.loads(os.path.expandvars(raw))
except (OSError, ValueError):
    sys.exit(0)  # 文件/JSON 合法性已由上文校验并另行失败,此处不重复报错
checks = [
    ("embedding", "18351", cfg.get("embedding", {}).get("dense", {}).get("model", "")),
    ("rerank", "18352", cfg.get("rerank", {}).get("model", "")),
]
for label, port, model in checks:
    if not model:
        continue
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as r:
            data = json.load(r)
        items = data.get("data") or data.get("models") or []
        ids = " ".join(str(m.get("id") or m.get("name") or "") for m in items).lower()
        if model.lower() in ids:
            continue
        detail = ids or "(加载列表为空)"
    except Exception as exc:
        detail = f"查询失败({type(exc).__name__}),跳过核对"
    print(f"PREPARE_WARN: {label} 上游({port})/v1/models 未含 {model!r}({detail});"
          f"模型名可能漂移,请人工确认", file=sys.stderr)
PY

# ---------- 目录与状态标记 ----------
ensure_dir_700() {
  mkdir -p "$1"
  chmod 700 "$1"
  [ "$(stat -c '%u:%g' "$1")" = "$EXPECTED_UID_GID" ] \
    || fail "$1 属主不是 $EXPECTED_UID_GID"
}
ensure_dir_700 "$STATE_DIR"
ensure_dir_700 "$SECRETS_DIR"
ensure_dir_700 "$BACKUP_DIR"

if [ -e "$MARKER" ]; then
  marker_status="已存在(继续接管本方案状态)"
else
  for entry in "$STATE_DIR"/* "$STATE_DIR"/.[!.]* "$STATE_DIR"/..?*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    case "$(basename "$entry")" in
      secrets) ;;  # 本脚本此前创建,允许
      *) fail "state 目录含非本方案创建的数据($(basename "$entry"));请先备份并人工确认,本脚本不清空、不接管" ;;
    esac
  done
  printf 'openviking-compose-v1\ncreated=%s\n' "$(date -Is)" > "$MARKER"
  chmod 600 "$MARKER"
  marker_status="已创建"
fi

echo "prepare 完成:"
echo "  - secrets.env:${root_key_status};权限 600"
echo "  - 目录 state/、state/secrets/、backups/:700,属主 ${EXPECTED_UID_GID};状态标记 ${marker_status}"
echo "  - 端口:1933/18353 空闲;18351/18352 由 model-serving 提供"
