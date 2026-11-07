#!/usr/bin/env bash
# 纯函数库:不得在此加载任何 ROOT/ADMIN/USER key 或执行命令矩阵。
# code_auth <header 行> <curl 参数...>;header 经 stdin 传入,不进 argv。
code_auth() {
  local header=$1
  shift
  # curl 配置双引号串内只有 \" 与 \\ 是字面量;先转义,防止选项注入。
  local esc=${header//\\/\\\\}
  esc=${esc//\"/\\\"}
  # 转义不覆盖 CR/LF:curl 配置按行解析,header 串内换行会把后续内容当
  # 独立配置指令执行(url=/output= 等实测可注入,多出的传输照样发起)。
  # 密钥文件损坏含换行时必须拒绝构造配置,不带病执行(2026-11-07 检视)。
  case $esc in
    *$'\n'*|*$'\r'*)
      printf 'code_auth: header 含 CR/LF(密钥文件损坏?);拒绝构造 curl 配置\n' >&2
      printf '000'
      return 1
      ;;
  esac
  printf 'header = "%s"\n' "$esc" \
    | curl -q -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 -K - "$@"
}

# expect <描述> <实际> <允许的期望值,如 401 或 401|403>
# 两个 e2e 脚本共用的同一判定实现(2026-11-07 检视收编,与 _http_fakes.py
# 同一模式);计票入全局 EXPECT_FAILS,列宽经 EXPECT_WIDTH(默认 34)。
EXPECT_FAILS=0
EXPECT_WIDTH=${EXPECT_WIDTH:-34}
expect() {
  local desc=$1 got=$2 want=$3
  if [[ "$got" =~ ^(${want})$ ]]; then
    printf 'PASS  %-*s %s (want %s)\n' "$EXPECT_WIDTH" "$desc" "$got" "$want"
  else
    printf 'FAIL  %-*s %s (want %s)\n' "$EXPECT_WIDTH" "$desc" "$got" "$want"
    EXPECT_FAILS=$((EXPECT_FAILS+1))
  fi
}

# require_loopback_base <BASE>:本地 ROOT/ADMIN/USER key 只能发往 loopback;
# BASE 仅允许无路径的 http loopback URL,防止把 key 发往远端。
require_loopback_base() {
  [[ "$1" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$ ]] \
    || { echo "FAIL-SETUP: BASE 仅允许无路径的 http loopback URL" >&2; return 1; }
}
