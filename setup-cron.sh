#!/bin/bash
set -euo pipefail

# 脚本自身所在目录（用于定位同目录下的 update-openviking.sh，避免硬编码绝对路径）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${SCRIPT_DIR}/update-openviking.sh"
# 错开 ollama 的 6:00（同机两服务不同时拉镜像 / 重建），定在每天 6:30
CRON_LINE="30 6 * * * ${SCRIPT}"
MARKER="# openviking-auto-update"

if [ ! -x "$SCRIPT" ]; then
    echo "❌ 未找到 ${SCRIPT}，请先确保脚本存在且可执行"
    exit 1
fi

# 读取当前 crontab，移除旧的 openviking 条目，追加新条目后写回。
# `|| true` 兜底：首次运行时用户可能没有任何 crontab（crontab -l 返回非 0），
# 且 grep -v 对空输入也返回 1 —— 避免 set -euo pipefail 下中断脚本。
# 保持管道流式（不用命令替换 $()），以保留原 crontab 的空行与格式。
{ crontab -l 2>/dev/null | grep -v "$MARKER" || true; echo "${CRON_LINE} ${MARKER}"; } | crontab -

echo "✅ crontab 已更新："
echo ""
crontab -l 2>/dev/null | grep "$MARKER" || echo "(未找到 ${MARKER} 条目，请检查)"
echo ""
echo "每天早上 6:30 执行 ${SCRIPT}"
