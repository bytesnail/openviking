#!/usr/bin/env bash
# OpenViking Compose 部署前置准备(设计 §7.1、§5 约束)。
#
# 纪律:
# - 绝不 source secrets.env,绝不回显任何变量值;
# - root key 缺失则生成,存在但为空则失败,重复执行幂等;
# - state 含未知数据时停止,不清空、不接管,且拒绝前不写 key/目录;
# - 端口 1933/18353 必须空闲,18351/18352 必须仅由 loopback 模型服务提供。
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
ENV_TOOL="$SCRIPT_DIR/env_file.py"
# Compose 中 openviking service 的 user(设计 §5:主服务以宿主 meteor 的 1000:1000 运行)
EXPECTED_UID_GID="1000:1000"

fail() { echo "prepare 失败:$*" >&2; exit 1; }

# ---------- 配置文件形态(防止 Compose 把缺失挂载源创建成目录) ----------
[ -f "$COMPOSE_FILE" ] || fail "缺少 $COMPOSE_FILE"
[ -f "$CONFIG_FILE" ] || fail "缺少 $CONFIG_FILE(必须是常规文件,不能是目录)"
[ -f "$SECRETS_FILE" ] || fail "缺少 $SECRETS_FILE"
[ -f "$ENV_TOOL" ] || fail "缺少 $ENV_TOOL"

# ---------- 宿主 UID/GID 与 Compose 一致性 ----------
current_uid_gid="$(id -u):$(id -g)"
[ "$current_uid_gid" = "$EXPECTED_UID_GID" ] \
  || fail "当前用户 $current_uid_gid 与 Compose user $EXPECTED_UID_GID 不一致;其他用户部署须先显式调整 compose.yaml 与本脚本常量"

# ---------- 端口与网络边界 ----------
command -v ss >/dev/null 2>&1 \
  || fail "缺少 ss,无法验证端口边界(见 README 前提);不安装就不放行"

port_listening() { ss -H -tln "sport = :$1" 2>/dev/null | grep -q .; }
loopback_only_listener() {
  local port=$1 local_addr listeners ipv4_seen=0
  # 一次 ss 取同一快照:存在性判定与地址枚举不得取自两次独立探测;
  # 快照为空(监听者恰在探测间退出,或 ss 出错被吞)时判失败,绝不"未验证即成功"。
  listeners="$(ss -H -tln "sport = :$port" 2>/dev/null)" || return 1
  [ -n "$listeners" ] || return 1
  while read -r _ _ _ local_addr _; do
    case "$local_addr" in
      # compose 与 ov.conf 都以 IPv4 127.0.0.1 直连上游,::1-only 监听
      # 本部署用不了,不能只凭它放行。
      "127.0.0.1:$port") ipv4_seen=1 ;;
      "[::1]:$port"|"::1:$port") ;;
      *) return 1 ;;
    esac
  done <<< "$listeners"
  [ "$ipv4_seen" = 1 ]
}
for p in 1933 18353; do
  if port_listening "$p"; then
    fail "端口 $p 已被占用(应为 OpenViking 部署保留);不杀既有进程,请先排查"
  fi
done
for p in 18351 18352; do
  if ! loopback_only_listener "$p"; then
    fail "端口 $p 未仅由 127.0.0.1 提供 loopback 服务(compose/ov.conf 以 127.0.0.1 直连,仅 ::1 不算);本方案不修改 model-serving"
  fi
done

# ---------- 在任何写入前验证 state 标记与未知内容 ----------
marker_status="待创建"
if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then
  [ -f "$MARKER" ] && [ ! -L "$MARKER" ] \
    || fail "state 标记不是常规文件或含符号链接;请先备份并人工确认"
  grep -Fqx 'openviking-compose-v1' "$MARKER" \
    || fail "state 标记内容无效;请先备份并人工确认,本脚本不接管"
  marker_status="已存在并验证(继续接管本方案状态)"
else
  [ ! -L "$STATE_DIR" ] \
    || fail "state 是符号链接;目录/密钥写入会穿透到链接目标逃出本仓库沙盒,请先备份并人工确认"
  [ ! -e "$STATE_DIR" ] || [ -d "$STATE_DIR" ] \
    || fail "state 路径存在但不是目录"
  if [ -d "$STATE_DIR" ]; then
    for entry in "$STATE_DIR"/* "$STATE_DIR"/.[!.]* "$STATE_DIR"/..?*; do
      [ -e "$entry" ] || [ -L "$entry" ] || continue
      case "$(basename "$entry")" in
        secrets)
          # 仅允许上次失败后留下的空目录;任何已有内容都属于未知状态。
          [ -d "$entry" ] && [ -z "$(find "$entry" -mindepth 1 -print -quit)" ] \
            || fail "state/secrets 含非本方案验证的数据;请先备份并人工确认"
          ;;
        *) fail "state 目录含非本方案创建的数据($(basename "$entry"));请先备份并人工确认,本脚本不清空、不接管" ;;
      esac
    done
  fi
fi

# ---------- secrets.env 解析(只解析,不执行) ----------
# 统一由 scripts/env_file.py 按 Compose env_file 常用语义解析(缩进、export、
# KEY = value、最后一条生效、引号/行内注释);失败只输出 PREPARE_FAIL,不回显值。
env_action="$(python3 -I -B "$ENV_TOOL" precheck "$SECRETS_FILE")"
case "$env_action" in
  GENERATE)
    # 先收紧权限再写新 key,避免 644 窗口;直接追加,不动已有内容。
    chmod 600 "$SECRETS_FILE"
    new_key="$(python3 -I -B -c 'import secrets; print(secrets.token_hex(32))')"
    last_char="$(tail -c 1 "$SECRETS_FILE")"
    if [ -n "$last_char" ]; then
      printf '\n' >> "$SECRETS_FILE"
    fi
    printf 'OPENVIKING_ROOT_API_KEY=%s\n' "$new_key" >> "$SECRETS_FILE"
    root_key_status="已生成(32 字节 hex)"
    ;;
  KEEP)
    root_key_status="已存在,保持不变"
    ;;
  *)
    fail "内部错误:env precheck 返回 '$env_action'"
    ;;
esac
chmod 600 "$SECRETS_FILE"

# ---------- 展开后配置可解析且密钥位无未展开字面值;模型名漂移预警 ----------
python3 -I -B "$ENV_TOOL" validate "$SECRETS_FILE" "$CONFIG_FILE"

# ---------- 目录与状态标记 ----------
ensure_dir_700() {
  # 与 state 标记同样拒绝符号链接:mkdir/chmod 都会跟随链接改写目标目录。
  [ ! -L "$1" ] || fail "$1 是符号链接;请先备份并人工确认,本脚本不接管"
  mkdir -p "$1"
  chmod 700 "$1"
  [ "$(stat -c '%u:%g' "$1")" = "$EXPECTED_UID_GID" ] \
    || fail "$1 属主不是 $EXPECTED_UID_GID"
}
ensure_dir_700 "$STATE_DIR"
ensure_dir_700 "$SECRETS_DIR"
ensure_dir_700 "$BACKUP_DIR"

if [ "$marker_status" = "待创建" ]; then
  printf 'openviking-compose-v1\ncreated=%s\n' "$(date -Is)" > "$MARKER"
  chmod 600 "$MARKER"
  marker_status="已创建"
fi

echo "prepare 完成:"
echo "  - secrets.env:${root_key_status};权限 600"
echo "  - 目录 state/、state/secrets/、backups/:700,属主 ${EXPECTED_UID_GID};状态标记 ${marker_status}"
echo "  - 端口:1933/18353 空闲;18351/18352 仅由 loopback 模型服务提供"
