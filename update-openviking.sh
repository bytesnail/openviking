#!/bin/bash
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"  # 脚本所在目录即 compose 目录，避免硬编码绝对路径
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"
LOG_FILE="${COMPOSE_DIR}/update-openviking.log"
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
cd "$COMPOSE_DIR"

if ! docker compose version &>/dev/null; then
    log "❌ docker compose 不可用，退出"
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    log "❌ 未找到 ${COMPOSE_FILE}，退出"
    exit 1
fi

rotate_log

# ── 拉取新镜像 ────────────────────────────────────────
log "开始拉取新镜像…"
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
