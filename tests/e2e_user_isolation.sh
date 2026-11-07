#!/usr/bin/env bash
# 双向 USER 私有区读写隔离验收;密钥只经文件变量和 curl stdin 使用。
set -u
cd "$(dirname "$0")/.." || { echo "FAIL-SETUP: 无法进入仓库根目录" >&2; exit 1; }
source tests/curl_auth.sh

USER_A=${USER_A:-}
USER_B=${USER_B:-}
USER_A_KEY_FILE=${USER_A_KEY_FILE:-}
USER_B_KEY_FILE=${USER_B_KEY_FILE:-}
BASE=${BASE:-http://127.0.0.1:1933}
if [[ ! "$USER_A" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
    || [[ ! "$USER_B" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
    || [[ "$USER_A" == "$USER_B" ]]; then
  echo "FAIL-SETUP: USER_A/USER_B 须为两个不同的安全 user_id" >&2
  exit 1
fi
require_loopback_base "$BASE" || exit 1
if [ ! -r "$USER_A_KEY_FILE" ] || [ ! -r "$USER_B_KEY_FILE" ]; then
  echo "FAIL-SETUP: 两个 USER key 来源必须可读(支持 /dev/fd)" >&2
  exit 1
fi
USER_A_KEY=$(cat "$USER_A_KEY_FILE")
USER_B_KEY=$(cat "$USER_B_KEY_FILE")
if [ -z "$USER_A_KEY" ] || [ -z "$USER_B_KEY" ]; then
  echo "FAIL-SETUP: 两个 USER key 均须非空" >&2
  exit 1
fi
SUFFIX="$(date +%s)-$$-${RANDOM}"
URI_A="viking://user/${USER_A}/resources/identity-isolation-${SUFFIX}-a.md"
URI_B="viking://user/${USER_B}/resources/identity-isolation-${SUFFIX}-b.md"
# expect/require_loopback_base 来自共享纯函数库(2026-11-07 检视收编);
# 默认列宽 34 与本脚本原输出一致,计票在全局 EXPECT_FAILS。

write_fixture() { # write_fixture <key> <uri> <A|B>
  local key=$1 uri=$2 marker=$3 body
  printf -v body '{"uri":"%s","content":"identity isolation %s %s","mode":"replace","wait":true,"processing_mode":"vectors_only"}' \
    "$uri" "$marker" "$SUFFIX"
  # wait:true 的写与 delete_fixture 同一超时预算(-m 300):code_auth 固化的
  # -m 15 是读接口预算,向量化写在管道繁忙时会超过它,把隔离验收误判为 FAIL
  # (2026-11-07 检视;后置 -m 覆盖 code_auth 的前置 -m,curl 取最后一值)。
  code_auth "X-API-Key: $key" -X POST -H 'Content-Type: application/json' \
    --data "$body" -m 300 "$BASE/api/v1/content/write"
}

read_fixture() { # read_fixture <key> <uri>
  code_auth "X-API-Key: $1" --get --data-urlencode "uri=$2" \
    "$BASE/api/v1/content/read"
}

delete_fixture() { # delete_fixture <key> <uri>
  code_auth "X-API-Key: $1" -X DELETE --get \
    --data-urlencode "uri=$2" --data-urlencode 'recursive=true' \
    --data-urlencode 'wait=true' -m 300 "$BASE/api/v1/fs"
}

cleanup() {
  trap - EXIT
  expect "cleanup A private fixture" "$(delete_fixture "$USER_A_KEY" "$URI_A")" '200|404'
  expect "verify A fixture removed" "$(read_fixture "$USER_A_KEY" "$URI_A")" 404
  expect "cleanup B private fixture" "$(delete_fixture "$USER_B_KEY" "$URI_B")" '200|404'
  expect "verify B fixture removed" "$(read_fixture "$USER_B_KEY" "$URI_B")" 404
}
trap cleanup EXIT

expect "A writes own private fixture" "$(write_fixture "$USER_A_KEY" "$URI_A" A)" 200
expect "B writes own private fixture" "$(write_fixture "$USER_B_KEY" "$URI_B" B)" 200
expect "A reads own private fixture" "$(read_fixture "$USER_A_KEY" "$URI_A")" 200
expect "B reads own private fixture" "$(read_fixture "$USER_B_KEY" "$URI_B")" 200
expect "A cannot read B private fixture" "$(read_fixture "$USER_A_KEY" "$URI_B")" '403|404'
expect "B cannot read A private fixture" "$(read_fixture "$USER_B_KEY" "$URI_A")" '403|404'

cleanup
unset USER_A_KEY USER_B_KEY
if [ "$EXPECT_FAILS" -eq 0 ]; then
  echo "SUMMARY: bidirectional private isolation and cleanup passed"
  exit 0
else
  echo "SUMMARY: $EXPECT_FAILS isolation/cleanup checks failed"
  exit 1
fi
