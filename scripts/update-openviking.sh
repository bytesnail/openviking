#!/bin/bash
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"  # 脚本在 scripts/，..=项目根(compose 所在)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"      # 脚本自身目录（scripts/）：log/lock 等运行产物写这里，保持项目根干净
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"
LOG_FILE="${SCRIPT_DIR}/update-openviking.log"
MAX_LOG_LINES=80000   # 日志超过此行数则裁掉前一半（约 8 万行，数月日志量）
CONTAINER="openviking"
HEALTH_TIMEOUT=150    # up 后等待容器变 healthy 的最长秒数（healthcheck start_period 30s + 若干次 30s interval）

# ── 日志函数 ──────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── 日志轮转：超过上限裁掉前一半 ──────────────────────
rotate_log() {
    if [ -f "$LOG_FILE" ]; then
        local lines
        lines=$(wc -l < "$LOG_FILE")
        if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
            tail -n "$((lines / 2))" "$LOG_FILE" > "${LOG_FILE}.tmp"
            mv "${LOG_FILE}.tmp" "$LOG_FILE"
            log "日志已轮转（原 ${lines} 行 → $(wc -l < "$LOG_FILE") 行）"
        fi
    fi
}

# ── 等待容器 healthcheck 转为 healthy ─────────────────
# 注意：openviking 的 healthcheck 只查 /health 端口（不验证 ${VAR} 展开 / 后端连通，见 CLAUDE.md 坑 #2），
# 所以 healthy 之后还要再跑一次 doctor。
wait_healthy() {
    local elapsed=0 status
    while true; do
        status="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "")"
        if [ "$status" = "healthy" ]; then
            log "✅ 容器已 healthy（耗时约 ${elapsed}s）"
            return 0
        fi
        if [ "$elapsed" -ge "$HEALTH_TIMEOUT" ]; then
            log "❌ 容器在 ${HEALTH_TIMEOUT}s 内未变 healthy（当前状态: ${status:-未知}），退出"
            return 1
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
}

# ── 前置检查 ──────────────────────────────────────────
# 版本 tag 必须显式指定（不再默认 latest —— latest 是滚动 tag，可能指向含 bug 的版本，
# 详见 CLAUDE.md 坑 #11：v0.4.4 的 role.value bug）。例：./update-openviking.sh v0.4.3
TAG="${1:-}"
if [ -z "$TAG" ]; then
    echo "用法: $0 <image-tag>   例: $0 v0.4.3   （支持 v0.4.3 / v0.4.4 / latest / main 等）" >&2
    exit 1
fi
IMAGE="openviking/openviking:${TAG}"

cd "$COMPOSE_DIR"

if ! docker compose version &>/dev/null; then
    log "❌ docker compose 不可用，退出"
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    log "❌ 未找到 ${COMPOSE_FILE}，退出"
    exit 1
fi

# secrets.env 是 ov.conf 里 ${VAR} 占位符的唯一来源（docker-compose env_file 注入）。
# 缺失时容器照常 healthy，但所有 ${VAR} 停留在字面值、后端全 401（见 CLAUDE.md 坑 #2）—— up 之前先拦下。
if [ ! -f "${COMPOSE_DIR}/secrets.env" ]; then
    log "❌ 未找到 secrets.env —— \${VAR} 占位符不会被展开（坑 #2），容器会 healthy 但后端全 401，退出"
    exit 1
fi

# 并发互斥：cron（6:30）与手动触发可能重叠，同一时间只允许一个实例。
# 否则 rotate_log 的 tail>tmp>mv 与另一实例的 tee -a 抢 inode 会丢日志行，且并发 compose 重建会互相竞争。
LOCK_FILE="${SCRIPT_DIR}/.update-openviking.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "⚠️ 另一个 openviking 更新实例正在运行，跳过本次" >&2
    exit 0
fi

rotate_log

# ── 更新 compose 镜像 tag 并拉取 ──────────────────────
# sed 改 docker-compose.yml 的 image 行到指定 tag（支持 v0.4.3 / latest / main 等），
# 这样 compose 永远锁在显式版本，cron/手动触发都不会被滚动的 latest 带走。
log "将镜像 tag 设为 ${IMAGE} 并拉取…"
sed -i "s|image: openviking/openviking:.*|image: ${IMAGE}|" "$COMPOSE_FILE"
if ! docker compose -f "$COMPOSE_FILE" pull 2>&1 | tee -a "$LOG_FILE"; then
    log "❌ 拉取镜像失败，退出"
    exit 1
fi

# ── 使用 docker compose up 重建有变更的容器 ───────────
# up -d 会重建容器，等同 down+up —— 这同时让 ov.conf / secrets.env 的变更生效（坑 #1）。
log "检查并重建需要更新的容器…"
if ! docker compose -f "$COMPOSE_FILE" up -d --remove-orphans 2>&1 | tee -a "$LOG_FILE"; then
    log "❌ up 执行出错"
    exit 1
fi

# ── 健康验证（openviking 特有，区别于 ollama）─────────
log "等待容器健康…"
if ! wait_healthy; then
    exit 1
fi

# doctor 做端到端探测：展开 ${VAR} + probe embedding/vlm 连通（CLAUDE.md 推荐的最可靠验证）。
# 失败只告警不退出 —— 它依赖本机后端（systemd qwen-llama@embedding-gpu / reranker-gpu、远程 kimi），
# 任一未就绪都会失败，但这不代表 openviking 镜像更新本身失败。
log "运行 doctor 做端到端连通验证…"
if docker exec "$CONTAINER" openviking-server doctor 2>&1 | tee -a "$LOG_FILE"; then
    log "✅ doctor 通过"
else
    log "⚠️ doctor 未完全通过（可能是后端服务未就绪或密钥未注入，非镜像更新问题）—— 请人工核查"
fi

# ── 清理未被任何容器使用的旧镜像 ──────────────────────
log "清理悬空镜像…"
docker image prune -f 2>&1 | tee -a "$LOG_FILE" || true

log "———————— 本轮结束 ————————"
