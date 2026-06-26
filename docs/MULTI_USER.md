# OpenViking 多 User(多租户)部署与使用

> **版本基线**:`openviking/openviking:v0.4.3`(2026-06 实测验证)。
> **定位**:openviking 多租户机制的**完整设置 / 部署 / 使用指南**——这是把 openviking 正式接入多个 MCP client(opencode / claude code / hermes / openclaw 等)、隔离不同软件与不同 agent 记忆的基础。
> 相关:`E2E_TESTING.md`(端到端测试)、`CLAUDE.md`(坑 + 架构)。官方文档:[Authentication and Multi-Tenancy](https://zread.ai/volcengine/OpenViking/21-authentication-and-multi-tenancy)。

---

## TL;DR

- **三层模型**:`account`(物理隔离)→ `user`(记忆空间 `viking://user/{user}/`)→ `peer`(user 内子协作)。
- **推荐方案**:`auth_mode:"api_key"` + **每个 client 一个 user + 专属 user key**。user key 格式 `base64(account).base64(user).base64(secret)`,**自带身份**——用它调 `/mcp` 自动路由到该 user,**无需任何 header**,天然隔离。
- **不要用 root_api_key 跑业务**:它路由到 default user、无隔离,且新版会禁止 root 访问数据 API。root 只用于 admin 管理。
- **实测**:opencode 与 claude-code 两个 user 互不可见记忆 ✅。

---

## 1. 为什么需要多 user

openviking 是"上下文数据库",所有 MCP client 默认往**同一个 user 的记忆空间**写。若 opencode / claude code / hermes / openclaw 都用同一个 key,它们的记忆会**混在一起**:
- claude code 记的"用户偏好"会污染 opencode 的检索;
- 同一个软件的多个 agent(如两个 claude code 会话)也互相串记忆。

多 user 让每个 client/agent 拥有**独立记忆空间**,互不可见、互不污染。

---

## 2. 三层租户模型:account / user / peer

| 层 | 含义 | 隔离粒度 | URI / 物理路径 |
|---|---|---|---|
| **account** | 顶层租户(可理解为一个团队/工作区) | **物理隔离**:`/local/{account_id}/...` 独立目录 + 独立 vectordb | URI 不含 account,物理层按 account 分 |
| **user** | account 内的用户(client/agent 身份) | 记忆空间隔离 | `viking://user/{user_id}/memories`、`/resources`、`/peers`、`/skills` |
| **peer** | user 内的子协作实体(群聊/多 agent 视图) | 视图过滤(不改身份) | `viking://user/{user_id}/peers/{peer_id}/...` |

> 单机个人使用通常 **一个 account + 多个 user**(每个 client/agent 一个 user)即可。account 用于彻底隔离不同团队/项目。

**关键**:URI 里只有 `user_id`(`viking://user/{user}/...`),account 隔离在**物理存储层**(`/local/{account}/`)。两个 account 下同名 user `alice` 数据物理隔离,URI 看起来相同但实际不同。

---

## 3. Auth 模式(`server.auth_mode`)

三种(`server/identity.py:AuthMode`):

| 模式 | 鉴权方式 | 适用 | 网络约束 |
|---|---|---|---|
| **`api_key`** | `X-API-Key` 或 `Authorization: Bearer <key>`(root 或 user key) | **生产多租户(推荐)** | 任意网络 |
| **`trusted`** | 信任 `X-OpenViking-Account`/`X-OpenViking-User` header(+ 可选 root key) | 反向代理/网关后(网关注入身份) | 非 localhost **必须**配 root_api_key |
| **`dev`** | 无鉴权,所有请求当 ROOT | 仅本地调试 | localhost only |

**自动检测**(不设 `auth_mode` 时):设了 `root_api_key` → `api_key`;没设 → `dev`。

**本仓当前**:`auth_mode:"api_key"` + `root_api_key`(`secrets.env`)。要上多 user,**保持 api_key 模式**,只需用 admin API 建 user、发 user key。

> `trusted` 模式仅当 openviking 在身份注入网关(OAuth proxy/服务网格)后才用——网关认证后注入 header,openviking 信任。个人单机直连**用 api_key + user key 更简单安全**,不需要 trusted。

---

## 4. user api key:自带身份的隔离单元(核心)

**user key 格式**(`server/api_keys/new.py:generate_api_key`):

```
base64url(account_id) . base64url(user_id) . base64url(secret)
例: ZTJlLXRlYW0.b3BlbmNvZGU.<secret>   (= e2e-team . opencode . secret)
```

**身份编码在 key 里**。调用时 `api_key_manager.resolve()` 直接从 key 解码出 account/user,**自动路由**:

| key 类型 | resolve 结果 | 能访问 |
|---|---|---|
| **root_api_key** | `role=ROOT`,account/user=None(填 default) | **admin API**;数据 API(`/mcp`)v0.4.3 能访问(路由 default),**新版禁止** |
| **user key** | `role=USER/ADMIN, account=X, user=Y` | **自动路由到 user Y**;数据 API 正常;admin 仅限本 account(ADMIN) |

**结论:每个 client 用自己的 user key 调 `/mcp`,自动落到 `viking://user/{该user}/...`,无需 `X-OpenViking-*` header。**

---

## 5. 管理:admin API(用 root_api_key)

所有管理路由前缀 `/api/v1/admin`,需 `X-API-Key: <root_api_key>`。

| 操作 | 方法 + 路径 | 权限 |
|---|---|---|
| 创建 account(+首个 admin user) | `POST /api/v1/admin/accounts` | ROOT |
| 列 account | `GET /api/v1/admin/accounts` | ROOT |
| 删 account(**级联清理存储+向量**) | `DELETE /api/v1/admin/accounts/{id}` | ROOT |
| 注册 user(返回 user key) | `POST /api/v1/admin/accounts/{id}/users` | ROOT/ADMIN |
| 列 account 下 user | `GET /api/v1/admin/accounts/{id}/users` | ROOT/ADMIN |
| 删 user | `DELETE /api/v1/admin/accounts/{id}/users/{uid}` | ROOT/ADMIN |
| **轮换 user key**(旧 key 立即失效) | `POST /api/v1/admin/accounts/{id}/users/{uid}/key` | ROOT/ADMIN |
| 改 user 角色 | `PUT /api/v1/admin/accounts/{id}/users/{uid}/role` | ROOT |

> user 必须由 admin API **预建**(`register_user` 同时建目录 + 发 key),不存在"首次用 key 自动建"。

---

## 6. 部署指南(单机多 client 隔离记忆)

### 6.1 ov.conf(保持现状即可)

```jsonc
"server": {
  "host": "127.0.0.1",        // 本仓现值;此字段被 OPENVIKING_SERVER_HOST 覆盖、不决定 bind(坑 #12)
  "auth_mode": "api_key",     // 已有
  "root_api_key": "${OPENVIKING_ROOT_API_KEY}"   // 已有,只用于 admin
}
```

### 6.2 建 account + 各 client 的 user(一次性)

```bash
set -a; source /mnt/hdd/tools/openviking/secrets.env; set +a
ROOT="$OPENVIKING_ROOT_API_KEY"; B="http://127.0.0.1:1933"

# ① 建 account(用一个 account 装所有 client user)
curl -s -X POST $B/api/v1/admin/accounts -H "X-API-Key: $ROOT" \
  -H "Content-Type: application/json" \
  -d '{"account_id":"myhome","admin_user_id":"admin"}'

# ② 给每个 client 建 user(各返回独立 user_key,妥善保存!)
for U in opencode claude-code hermes openclaw; do
  echo "=== $U ==="
  curl -s -X POST $B/api/v1/admin/accounts/myhome/users -H "X-API-Key: $ROOT" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\":\"$U\",\"role\":\"user\"}"
  echo
done
```

> 同一软件的多个 agent(如两个 claude code 会话)要隔离,就建 `claude-code-1`/`claude-code-2` 等 user。

### 6.3 接入 MCP client:两种方式(关键:选对 auth 模式)

隔离 user 有两种方式,**取决于 ov.conf 的 `auth_mode`**:

| 方式 | auth_mode | client 怎么指定 user | 适用 |
|---|---|---|---|
| **A. user key 自带身份** | `api_key`(当前) | 每个 client 用自己的 **user key**(`X-API-Key`/`Authorization`),**不发 header**,key 自动路由 | 自定义 MCP 配置、直接 HTTP、自写集成 |
| **B. header 断言身份** | `trusted` | client 发 `X-OpenViking-Account`/`X-OpenViking-User` header(+ root key) | **官方插件**(claude-code/opencode 等都发 header) |

> ⚠️ **官方插件 ≠ api_key 模式**:实测 api_key 模式带 `X-OpenViking-User` 一律 `403 can only assert identity in trusted mode`(不管 user key 还是 root、不管一致与否)。**官方 `claude-code-memory-plugin`/`opencode-memory-plugin` 固定发 header → 要用它们必须切 trusted 模式(§6.4)**。留在 api_key 模式就只能用方式 A(自定义 MCP 配 user key,不发 header)。

#### 方式 A:api_key 模式 + user key(自定义 MCP,当前模式)

每个 client 的 MCP 配置填自己的 user_key,**不发任何 X-OpenViking-* header**:

**Claude Code**(`~/.claude.json` 或项目 `.mcp.json`):
```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://127.0.0.1:1933/mcp",
      "headers": { "X-API-Key": "<该 client 的 user_key>" }
    }
  }
}
```

**任何支持远程(streamable HTTP)MCP 的 client**(opencode / hermes / cursor / 自写脚本):在各自 MCP server 配置里填 `url` + `X-API-Key: <user_key>`(或 `Authorization: Bearer <user_key>`)。**不要**加 `X-OpenViking-Account/User`(api_key 模式会 403)。

**关键**:每个 client 只换 `user_key` 这一项,openviking 按 key 自动路由,记忆隔离。

#### 方式 B:trusted 模式 + header(官方插件,体验最佳)

官方 `examples/claude-code-memory-plugin`、`examples/opencode-memory-plugin` 用 `ovcli.conf`/env + `X-OpenViking-Account/User` header 隔离(带自动 recall/capture,体验比纯 MCP 工具好)。要用它们:

1. ov.conf 切 trusted(§6.4);
2. 装 plugin(各 plugin README 有 `setup-helper/install.sh` 一键脚本,或 `claude plugin install claude-code-memory-plugin@openviking-plugins-local`);
3. `~/.openviking/ovcli.conf` 填连接 + 每个 client/agent 自己的 account/user:
   ```json
   { "url":"http://127.0.0.1:1933","api_key":"<root_api_key>","account":"myhome","user":"claude-code" }
   ```
   或用 env `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER`(每个 client 设自己的 `OPENVIKING_USER`)。

> openclaw 见 `examples/openclaw-plugin`;codex CLI 见 `examples/codex-memory-plugin`——同样是 url+身份(header/env)模式,装各自 plugin 即可。

### 6.4 切 trusted 模式(用官方插件 / 方式 B 时)

ov.conf `server` 段改 `auth_mode`:
```jsonc
"server": { "host":"0.0.0.0", "auth_mode":"trusted", "root_api_key":"${OPENVIKING_ROOT_API_KEY}" }
```
`down && up -d`。此后信任 `X-OpenViking-Account/User` header(每请求仍需 root_api_key;非 localhost 强制要求 root key)。安全含义见 §9——trusted = 信任身份断言,只在可信网络/网关后用。

> 切 trusted 后,方式 A 的 user key 仍可用(user key 自带身份,trusted 也接受)——两种方式可并存。但 api_key 模式下方式 B(header)不行(403)。

---

## 7. 实测验证(本轮,v0.4.3)

```
建 account=e2e-team, user=opencode / claude-code(各自 user_key)
opencode key → remember "OPENCODE-MARKER-XYZ" → 落 viking://user/opencode/memories
opencode ls viking://user/opencode → [memories/resources/privacy/peers/skills] ✅
claude-code ls viking://user/claude-code → 自己的独立空间 ✅
claude-code search "OPENCODE-MARKER-XYZ" → 搜不到(只召回全局默认 .abstract.md) ✅ 隔离有效
删 account e2e-team → deleted:true(级联清理) → account 列表只剩 default ✅ 无残留
```

**结论:user key 自动路由 + 跨 user 隔离,方案完全可行。**

---

## 8. 日常运维

```bash
set -a; source /mnt/hdd/tools/openviking/secrets.env; set +a
ROOT="$OPENVIKING_ROOT_API_KEY"; B="http://127.0.0.1:1933"; ACC=myhome

# 加一个 user(如新装了 cursor)
curl -s -X POST $B/api/v1/admin/accounts/$ACC/users -H "X-API-Key: $ROOT" \
  -H "Content-Type: application/json" -d '{"user_id":"cursor","role":"user"}'

# user key 泄露 → 轮换(旧 key 立即失效)
curl -s -X POST $B/api/v1/admin/accounts/$ACC/users/claude-code/key -H "X-API-Key: $ROOT"

# 列所有 user(核查)
curl -s $B/api/v1/admin/accounts/$ACC/users -H "X-API-Key: $ROOT" | python3 -m json.tool

# 删 user(清理某 client)
curl -s -X DELETE $B/api/v1/admin/accounts/$ACC/users/cursor -H "X-API-Key: $ROOT"

# 删整个 account(级联清存储+向量,慎用)
curl -s -X DELETE $B/api/v1/admin/accounts/$ACC -H "X-API-Key: $ROOT"
```

---

## 9. 安全注意

1. **root_api_key 只做 admin**(建/删 user、轮换 key);**不要拿它配给 client 跑业务**——它路由到 default user、无隔离,且 openviking 新版会禁止 root 访问数据 API(`/mcp`)。本仓历史测试用 root 调 `/mcp` 能通是 v0.4.3 的行为,**升级后可能失效**,生产请一律用 user key。
2. **user key 即密码**:泄露即等于该 user 的全部记忆暴露。泄露立刻 `POST .../users/{uid}/key` 轮换。
3. **网络暴露**:本仓 1933 实际 bind 由 docker-compose.yml 的 `OPENVIKING_SERVER_HOST` 决定(优先于 ov.conf `server.host`,见 CLAUDE.md 坑 #12),当前 `127.0.0.1` = **仅本机、LAN 不可达**。若为多机/远程 client 改成 `0.0.0.0` 或某 LAN IP 开放访问,则 user key 是唯一防线,需配合防火墙 / 绑特定网卡收口。
4. **account 才是物理隔离**:不同 account 的数据连向量库都分开。要彻底隔离(如工作 vs 个人),用不同 account 而非同 account 不同 user。
5. **trusted 模式风险**:仅在可信网关后用;直连开 trusted = 任何人能伪造任意 user 身份(若没配 root key)。

---

## 附:三层身份速查

```
viking://user/{user_id}/memories          ← user 的长期记忆
viking://user/{user_id}/resources         ← user 的资源文件
viking://user/{user_id}/peers/{peer_id}/  ← user 内 peer(群聊/多 agent)
viking://resources/{name}                 ← 全局资源(跨 user 共享,所有 user 可读)

物理存储: /local/{account_id}/user/{user_id}/...   ← account 级物理隔离
vectordb:  按 account_id 绑定(_bound_account_id)   ← 向量也按 account 隔离
```
