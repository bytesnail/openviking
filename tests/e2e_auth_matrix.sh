#!/usr/bin/env bash
# 认证边界矩阵(宿主机执行):核对 api_key 模式下 key/角色与 API 面的访问关系。
#
# 前置:compose 两个 service 在位;secrets.env 含 OPENVIKING_ROOT_API_KEY,
# state/secrets/user_api_key 存在。注意:该文件对应当前 default 用户,实测
# role=admin(不是 role=user),因此本矩阵验证的是 ROOT 与 ADMIN 边界;普通
# USER 边界待三身份方案创建 test(role=user)后补测。
# 密钥只经变量中转并以 curl stdin 配置(-K -)传入,不进入 curl argv(进程
# 列表不可见),输出仅 HTTP 状态码,不读取、不回显任何凭据明文,不打印响应体
# (管理面响应可能含敏感字段)。
#
# 预期(源码 openviking/server/auth,docs/en/guides/04-authentication.md):
#   tenant 数据 API(/api/v1/fs/*)须 User/Admin key;ROOT key 被拒;
#   管理 API(/api/v1/admin/*)须 ROOT key;ADMIN key 被拒;
#   无 key / 错 key 一律 401;/studio 静态页无需认证。
#
# 用法:bash tests/e2e_auth_matrix.sh(在仓库根目录)
set -u
cd "$(dirname "$0")/.." || { echo "FAIL-SETUP: 无法进入仓库根目录" >&2; exit 1; }

read_secret() { # 与 scripts/prepare.sh read_var 同语义:取末次出现、剥引号与行内注释
  local raw double_quoted single_quoted
  raw="$(sed -n "s/^$1=//p" secrets.env | tail -n 1)"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  double_quoted='^("([^"\\]|\\.)*")[[:space:]]*(#.*)?$'
  single_quoted="^('[^']*')[[:space:]]*(#.*)?$"
  if [[ "$raw" =~ $double_quoted ]] || [[ "$raw" =~ $single_quoted ]]; then
    raw="${BASH_REMATCH[1]}"
    raw="${raw:1:${#raw}-2}"
  else
    raw="${raw%%[[:space:]]#*}"
    raw="${raw%"${raw##*[![:space:]]}"}"
  fi
  printf '%s' "$raw"
}

ROOT_KEY=$(read_secret OPENVIKING_ROOT_API_KEY)
[ -n "$ROOT_KEY" ] || { echo "FAIL-SETUP: secrets.env 缺少 OPENVIKING_ROOT_API_KEY(先执行 scripts/prepare.sh)" >&2; exit 1; }
[ -s state/secrets/user_api_key ] || { echo "FAIL-SETUP: state/secrets/user_api_key 缺失或为空(先执行 bootstrap_auth.py)" >&2; exit 1; }
ADMIN_KEY=$(cat state/secrets/user_api_key)  # default 用户的 key,role=admin
BASE=${BASE:-http://127.0.0.1:1933}
code() { curl -s -o /dev/null -w '%{http_code}' -m 15 "$@"; }
code_auth() { # code_auth <header 行> <curl 参数...>;header 经 stdin 传入,不进 argv
  local header=$1
  shift
  printf 'header = "%s"\n' "$header" \
    | curl -s -o /dev/null -w '%{http_code}' -m 15 -K - "$@"
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
expect "no-key  -> /studio"      "$(code "$BASE/studio")"                                                              200

unset ROOT_KEY ADMIN_KEY
if [ "$fails" -eq 0 ]; then echo "SUMMARY: all passed"; exit 0; else echo "SUMMARY: $fails failed"; exit 1; fi
