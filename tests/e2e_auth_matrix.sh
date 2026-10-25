#!/usr/bin/env bash
# 认证边界矩阵(宿主机执行):核对 api_key 模式下 key/角色与 API 面的访问关系。
#
# 前置:compose 两个 service 在位;secrets.env 含 OPENVIKING_ROOT_API_KEY,
# state/secrets/user_api_key 存在。密钥只经变量中转,输出仅 HTTP 状态码,
# 不读取、不回显任何凭据明文,不打印响应体(管理面响应可能含敏感字段)。
#
# 预期(源码 openviking/server/auth,docs/en/guides/04-authentication.md):
#   tenant 数据 API(/api/v1/fs/*)须 User/Admin key;ROOT key 被拒;
#   管理 API(/api/v1/admin/*)须 ROOT key;User key 被拒;
#   无 key / 错 key 一律 401;/studio 静态页无需认证。
#
# 用法:bash tests/e2e_auth_matrix.sh(在仓库根目录)
set -u
cd "$(dirname "$0")/.."

ROOT_KEY=$(grep -oP '(?<=^OPENVIKING_ROOT_API_KEY=)\S+' secrets.env | tr -d '"')
USER_KEY=$(cat state/secrets/user_api_key)
BASE=${BASE:-http://127.0.0.1:1933}
code() { curl -s -o /dev/null -w '%{http_code}' -m 15 "$@"; }
fails=0

expect() { # expect <描述> <实际> <允许的期望值,如 401 或 401|403>
  local desc=$1 got=$2 want=$3
  if [[ "$got" =~ ^(${want})$ ]]; then
    printf 'PASS  %-30s %s (want %s)\n' "$desc" "$got" "$want"
  else
    printf 'FAIL  %-30s %s (want %s)\n' "$desc" "$got" "$want"
    fails=$((fails+1))
  fi
}

expect "no-key  -> fs/ls"        "$(code "$BASE/api/v1/fs/ls?uri=viking://resources")"                 401
expect "bad-key -> fs/ls"        "$(code -H 'X-API-Key: invalid-test-key' "$BASE/api/v1/fs/ls?uri=viking://resources")" 401
expect "user-key(X-API-Key) fs"  "$(code -H "X-API-Key: $USER_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")"        200
expect "user-key(Bearer) fs"     "$(code -H "Authorization: Bearer $USER_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")" 200
expect "root-key -> fs/ls"       "$(code -H "X-API-Key: $ROOT_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")"        "401|403"
expect "root-key -> admin/accts" "$(code -H "X-API-Key: $ROOT_KEY" "$BASE/api/v1/admin/accounts")"                      200
expect "user-key -> admin/accts" "$(code -H "X-API-Key: $USER_KEY" "$BASE/api/v1/admin/accounts")"                      "401|403"
expect "no-key  -> /studio"      "$(code "$BASE/studio")"                                                              200

unset ROOT_KEY USER_KEY
if [ "$fails" -eq 0 ]; then echo "SUMMARY: all passed"; exit 0; else echo "SUMMARY: $fails failed"; exit 1; fi
