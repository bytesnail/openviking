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
if [[ ! "$BASE" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$ ]]; then
  echo "FAIL-SETUP: BASE 仅允许无路径的 http loopback URL" >&2
  exit 1
fi
if [ ! -s "$USER_A_KEY_FILE" ] || [ ! -s "$USER_B_KEY_FILE" ]; then
  echo "FAIL-SETUP: 两个 USER key 文件必须存在且非空" >&2
  exit 1
fi
USER_A_KEY=$(cat "$USER_A_KEY_FILE")
USER_B_KEY=$(cat "$USER_B_KEY_FILE")
SUFFIX="$(date +%s)-$$-${RANDOM}"
URI_A="viking://user/${USER_A}/resources/identity-isolation-${SUFFIX}-a.md"
URI_B="viking://user/${USER_B}/resources/identity-isolation-${SUFFIX}-b.md"
fails=0

expect() { # expect <描述> <实际> <允许状态>
  local desc=$1 got=$2 want=$3
  if [[ "$got" =~ ^(${want})$ ]]; then
    printf 'PASS  %-34s %s (want %s)\n' "$desc" "$got" "$want"
  else
    printf 'FAIL  %-34s %s (want %s)\n' "$desc" "$got" "$want"
    fails=$((fails+1))
  fi
}

write_fixture() { # write_fixture <key> <uri> <A|B>
  local key=$1 uri=$2 marker=$3 body
  printf -v body '{"uri":"%s","content":"identity isolation %s %s","mode":"replace","wait":true,"processing_mode":"vectors_only"}' \
    "$uri" "$marker" "$SUFFIX"
  code_auth "X-API-Key: $key" -X POST -H 'Content-Type: application/json' \
    --data "$body" "$BASE/api/v1/fs/write"
}

read_fixture() { # read_fixture <key> <uri>
  code_auth "X-API-Key: $1" --get --data-urlencode "uri=$2" \
    "$BASE/api/v1/fs/read"
}

delete_fixture() { # delete_fixture <key> <uri>
  code_auth "X-API-Key: $1" -X DELETE --get \
    --data-urlencode "uri=$2" --data-urlencode 'recursive=true' \
    --data-urlencode 'wait=true' "$BASE/api/v1/fs"
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
if [ "$fails" -eq 0 ]; then
  echo "SUMMARY: bidirectional private isolation and cleanup passed"
  exit 0
else
  echo "SUMMARY: $fails isolation/cleanup checks failed"
  exit 1
fi
