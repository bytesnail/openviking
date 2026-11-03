#!/usr/bin/env bash
# 纯函数库:不得在此加载任何 ROOT/ADMIN/USER key 或执行命令矩阵。
# code_auth <header 行> <curl 参数...>;header 经 stdin 传入,不进 argv。
code_auth() {
  local header=$1
  shift
  # curl 配置双引号串内只有 \" 与 \\ 是字面量;先转义,防止选项注入。
  local esc=${header//\\/\\\\}
  esc=${esc//\"/\\\"}
  printf 'header = "%s"\n' "$esc" \
    | curl -q -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 -K - "$@"
}
