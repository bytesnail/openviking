#!/usr/bin/env bash
# 认证边界矩阵(宿主机执行):核对 api_key 模式下 key/角色与 API 面的访问关系。
#
# 前置:compose 两个 service 在位;secrets.env 含 OPENVIKING_ROOT_API_KEY,
# state/secrets/user_api_key 存在。注意:该文件对应当前 default 用户,实测
# role=admin(不是 role=user)。矩阵默认验证 ROOT 与 ADMIN 边界;当
# state/secrets/test_user_api_key 存在时自动追加 USER 边界行(否则 SKIP)。
# 密钥只经变量中转并以 curl stdin 配置(-K -)传入,不进入 curl argv(进程
# 列表不可见);curl -q 必须为首个参数,禁止用户 curlrc 的 trace/verbose 泄密;
# 输出仅 HTTP 状态码,不打印/不回显凭据明文,不打印响应体
# (管理面响应可能含敏感字段)。本矩阵只验证状态边界,不替代方案 §6.3 的
# 双向私有读写隔离验收;REQUIRE_USER=1 可把缺失 USER 行变为失败。
#
# 预期(源码 openviking/server/auth,docs/en/guides/04-authentication.md):
#   tenant 数据 API(/api/v1/fs/*)须 User/Admin key;ROOT key 被拒;
#   账户生命周期 API(/api/v1/admin/accounts)须 ROOT key;ADMIN key 被拒;
#   v0.5.2.1 的 /api/v1/admin/accounts/<account>/users 等 user 管理子路径
#   允许 ADMIN(见方案 §2.4),矩阵对 ADMIN 断言该端点 200;
#   普通 USER 不可访问 /api/v1/admin/accounts*;
#   无 key / 错 key 一律 401;/studio 静态页无需认证。
#   "ADMIN" 行的判据是 fs 200 + admin/users 200 同时成立(USER 会在后者被拒,
#   ROOT 会在 fs 被拒),不只依赖 key 文件标签。
#
# 用法:bash tests/e2e_auth_matrix.sh(在仓库根目录)
set -u
cd "$(dirname "$0")/.." || { echo "FAIL-SETUP: 无法进入仓库根目录" >&2; exit 1; }

ENV_TOOL=scripts/env_file.py
[ -f "$ENV_TOOL" ] || { echo "FAIL-SETUP: 缺少 $ENV_TOOL" >&2; exit 1; }
ROOT_KEY=$(python3 -I -B "$ENV_TOOL" get secrets.env OPENVIKING_ROOT_API_KEY) \
  || { echo "FAIL-SETUP: 解析 secrets.env 失败" >&2; exit 1; }
[ -n "$ROOT_KEY" ] || { echo "FAIL-SETUP: secrets.env 缺少 OPENVIKING_ROOT_API_KEY(先执行 scripts/prepare.sh)" >&2; exit 1; }
[ -s state/secrets/user_api_key ] || { echo "FAIL-SETUP: state/secrets/user_api_key 缺失或为空(先执行 bootstrap_auth.py)" >&2; exit 1; }
ADMIN_KEY=$(cat state/secrets/user_api_key)  # default 用户的 key,role=admin
BASE=${BASE:-http://127.0.0.1:1933}
# 本地 ROOT/ADMIN key 只能发往 loopback;BASE 用于恢复/替代端口,不能指向远端。
if [[ ! "$BASE" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$ ]]; then
  echo "FAIL-SETUP: BASE 仅允许无路径的 http loopback URL" >&2
  exit 1
fi
# --noproxy '*':BASE 虽限 loopback,但设了 http_proxy/ALL_PROXY 的环境会把
# 请求(连 ROOT/ADMIN key 头)送经代理;与 bootstrap/adapter 的禁代理纪律一致。
code() { curl -q -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 "$@"; }
code_auth() { # code_auth <header 行> <curl 参数...>;header 经 stdin 传入,不进 argv
  local header=$1
  shift
  printf 'header = "%s"\n' "$header" \
    | curl -q -s --noproxy '*' -o /dev/null -w '%{http_code}' -m 15 -K - "$@"
}
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
expect "admin(X-API-Key) fs"     "$(code_auth "X-API-Key: $ADMIN_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")"        200
expect "admin(Bearer) fs"        "$(code_auth "Authorization: Bearer $ADMIN_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")" 200
expect "root-key -> fs/ls"       "$(code_auth "X-API-Key: $ROOT_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")"        "401|403"
expect "root-key -> admin/accts" "$(code_auth "X-API-Key: $ROOT_KEY" "$BASE/api/v1/admin/accounts")"                      200
expect "admin-key -> admin/accts" "$(code_auth "X-API-Key: $ADMIN_KEY" "$BASE/api/v1/admin/accounts")"                    "401|403"
# 管理面响应可能含明文 key;code_auth 已 -o /dev/null,不打印响应体。
expect "admin-key -> admin/users" "$(code_auth "X-API-Key: $ADMIN_KEY" "$BASE/api/v1/admin/accounts/default/users?include_credentials=false")"       200
expect "no-key  -> /studio"      "$(code "$BASE/studio")"                                                              200

USER_KEY_FILE=${USER_KEY_FILE:-state/secrets/test_user_api_key}
if [ -s "$USER_KEY_FILE" ]; then
  USER_KEY=$(cat "$USER_KEY_FILE")
  expect "user(X-API-Key) fs"    "$(code_auth "X-API-Key: $USER_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")"       200
  expect "user(Bearer) fs"       "$(code_auth "Authorization: Bearer $USER_KEY" "$BASE/api/v1/fs/ls?uri=viking://resources")" 200
  expect "user -> admin/accts"   "$(code_auth "X-API-Key: $USER_KEY" "$BASE/api/v1/admin/accounts")"                     "401|403"
  # v0.5.2.1:USER 不可列 user;升级到允许 USER 列 user 的版本后按版本调整。
  expect "user -> admin/users"   "$(code_auth "X-API-Key: $USER_KEY" "$BASE/api/v1/admin/accounts/default/users?include_credentials=false")"       "401|403"
  unset USER_KEY
else
  if [ "${REQUIRE_USER:-0}" = "1" ]; then
    echo "FAIL  USER boundary rows required but $USER_KEY_FILE absent (create per plan §6.1)"
    fails=$((fails+1))
  else
    echo "SKIP  USER boundary rows: $USER_KEY_FILE absent (create per plan §6.1; REQUIRE_USER=1 makes this fatal)"
  fi
fi

unset ROOT_KEY ADMIN_KEY USER_KEY_FILE
if [ "$fails" -eq 0 ]; then echo "SUMMARY: all executed checks passed"; exit 0; else echo "SUMMARY: $fails failed"; exit 1; fi
