#!/bin/bash
set -euo pipefail

# openviking 是纯 docker-compose 项目（单容器），清理 = 整个项目 down。
# 用 compose down 而非逐个 docker rm -f：语义更干净，也会顺带清掉 compose 残余；
# bind-mount 的 workspace/（vectordb 等运行态数据）与 ov.conf 默认不受影响（down 不删 volume / bind 目录）。
COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"  # 脚本在 scripts/，..=项目根(compose 所在)
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ 未找到 ${COMPOSE_FILE}，退出"
    exit 1
fi

echo "停止并移除 openviking compose 项目…"
docker compose -f "$COMPOSE_FILE" down --remove-orphans

echo ""
echo "—————— 完成 ——————"
echo ""
echo "确认已清除："
docker ps -a --filter "name=openviking" --format "table {{.Names}}\t{{.Status}}"
echo ""
echo "提示：workspace/（vectordb 等运行态数据）与 ov.conf 为 bind-mount，未被删除。"
echo "      需彻底重建 vectordb（如改了 embedding 维度）请手动删除 workspace/vectordb/context。"
