# OpenViking 官方 agent 接入是否需要 ADMIN：第一方来源核查

- **研究/访问日期**：2026-11-02
- **问题**：个人用户 `meteor` 若保持 `role=user`，能否使用官方安装器、CLI、MCP 与官方 agent 工具？哪些操作才真正需要 ROOT/ADMIN？
- **范围**：自托管 OpenViking 的 **API Key 模式**为主；覆盖官方统一安装器、CLI、内置 MCP，以及 Claude Code、Codex、OpenCode、pi、DeepSeek Harness（DSH）、Hermes 和 Agent Plugins 接入。Trusted/Dev 等模式只作为例外说明。
- **方法约束**：只采用 OpenViking 官方文档、官方仓库源码和官方安装脚本；未把本机部署配置或既有方案当作官方依据。未执行安装器，未调用管理 API，未创建/轮换用户或 key，未重启部署；仅对本机 `state/data/viking/default/_system/users.json` 做过只读的存储形态核对（未记录/回显任何凭据值），未对本机服务发起其他实测。

## 一句话结论

**日常 agent 接入不要求 ADMIN。** 在 API Key 模式下，官方明确接受绑定 account/user 的 **USER 或 ADMIN key** 做普通数据操作和 MCP；ROOT key 用于账户/部署管理，反而不能用于 tenant 数据面或 `/mcp`。因此，已经持有有效普通 USER key 的 `meteor` 保持 `role=user`，**不会阻碍所核查的官方安装器或工具接入**。需要 ROOT/ADMIN 的是初始注册/删除用户、改角色等管理操作；在 **v0.5.2.1** 上，轮换 `meteor` 的 key 也需要 ROOT/ADMIN。**注意：v0.5.2.1 的 ROOT/ADMIN 还可通过 `GET .../users`（默认 `include_credentials=true`）取得本账户所有 user 的明文 key，并轮换任何 user 的 key（§2.2/§5.2）；因此 ADMIN/ROOT key 在效果上仍是账户内数据的等价高权限凭据，不能当作“只能管理、不能读数据”的低风险身份。**

证据主干：[官方认证指南的两层 key 模型](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L46-L55)、[tenant 数据访问规则](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L158-L167)、[MCP Authentication](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/06-mcp-integration.md#L28-L35)。

## 1. 所查官方版本与动态材料

| 材料 | 本次固定版本 | 官方链接 |
|---|---|---|
| 最新稳定 release | **v0.5.2.1**，tag commit `f5e6203033a13469c3d9a937f2ca39492ea69c51`，发布于 2026-10-27；与当前部署版本号相同 | [release](https://github.com/volcengine/OpenViking/releases/tag/v0.5.2.1)、[tag commit](https://github.com/volcengine/OpenViking/commit/f5e6203033a13469c3d9a937f2ca39492ea69c51) |
| 本次核查 main（2026-11-01T16:29:23Z） | `de6a440390f169be83e29b62d26b647eadd86a53` | [main commit](https://github.com/volcengine/OpenViking/commit/de6a440390f169be83e29b62d26b647eadd86a53) |
| 2026-11-02 `git ls-remote` 的 main | `4b92b30656c821712e2cf5f053963dedc66206d9` | [main commit](https://github.com/volcengine/OpenViking/commit/4b92b30656c821712e2cf5f053963dedc66206d9) |
| 线上统一安装器 | `https://openviking.ai/install` 引导下载的 shared installer，`INSTALLER_VERSION="20261101-6b2bcf29b2"`；对应源码 commit `6b2bcf29b2f4169b519dd96015b50adb3dca08f2` | [bootstrap](https://openviking.ai/install)、[shared installer](https://docs.openviking.ai/dl/memory-plugin-shared/install.sh)、[源码 commit](https://github.com/volcengine/OpenViking/commit/6b2bcf29b2f4169b519dd96015b50adb3dca08f2)、[版本标识与源码对应规则](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L93-L106) |

线上 shared installer 与本次 main 的对应文件逐字比较仅有 `INSTALLER_VERSION` 一行不同；下文安装器行号使用其构建 commit `6b2bcf29…`，不是把 v0.5.2.1 的安装器误当成线上当前安装器。

## 2. 必须分开的两个面：账户/部署管理面与数据面

### 2.1 官方角色定义

| 角色/凭据 | 官方规定的用途 | 日常数据面 |
|---|---|---|
| ROOT / root key | 账户管理、Admin API 和选定的 system/monitoring 操作 | **API Key 模式下不能访问 tenant 数据** |
| ADMIN / 账户绑定 key | 普通操作 + 管理**自己账户**内的用户 | 可以，但以**自己的 user 身份**访问；在 v0.5.2.1 上还可列举本账户 user 明文 key/轮换他人 key，效果上等价于账户内数据访问（§2.2） |
| USER / 账户绑定 key | 普通操作，例如 `ls`、read、find、sessions | 可以，受命名空间与资源 ACL 约束 |

来源：[Authentication → Two-Layer API Key System](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L46-L55)、[Roles and Permissions](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L620-L630)。

服务端实现与文档一致：API Key 插件只给 ROOT 放行 `/api/v1/admin`、部分 system/monitoring 等路径；ROOT 请求其他 tenant 路径会报权限错误，并明确提示使用 user/admin key。[`openviking/server/auth/plugins/api_key.py` L17-L35](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/auth/plugins/api_key.py#L17-L35)、[同文件 L203-L217](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/auth/plugins/api_key.py#L203-L217)。

### 2.2 ADMIN 的直接数据请求不等于能读取其他 user 的私人数据

官方 User 指南把“数据 owner”与“管理角色”分开：USER 做个人数据和获授权的共享资源操作；ADMIN 增加账户成员和共享资源管理，但普通数据请求仍使用 ADMIN 自己的 User 身份，不能靠 identity headers 切换到别人。[User → Users and roles](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/getting-started/19-users.md#L64-L75)。

资源 ACL 是另一层授权，不应与账户角色混为一谈：普通数据操作仍受资源 ACL；ADMIN 对账户共享资源有隐式 `manage`，但资源 `manage` 本身并不赋予账户管理员权限。[Authentication L628](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L628-L630)、[MCP resource permissions](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/06-mcp-integration.md#L214-L229)、[ACL supported URIs](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/concepts/15-acl.md#L7-L19)。

**但“直接数据请求不能切身份”不等于 ADMIN 拿不到别人的数据（2026-11-02 源码 + 本机存储形态核对）**：在 v0.5.2.1 上，`GET /api/v1/admin/accounts/{id}/users` 默认 `include_credentials=true`，ROOT/ADMIN 的响应包含本账户所有 user 的**明文 `api_key`**；ADMIN 还可 `POST /api/v1/admin/accounts/{id}/users/{uid}/key` 轮换任何 user 的 key 并拿到新 key。用该 key 直接以目标 user 身份发请求即可读取其私有数据。当前 main 已改为 ROOT 取明文、ADMIN 只取自己的 key + 他人掩码前缀；该掩码差异**不能**倒推到 v0.5.2.1。因此 ADMIN/ROOT key 在效果上等价于账户内全部 user 数据的访问能力，必须按 crown-jewel 保护。

## 3. 官方统一安装器是否要求 ADMIN

### 3.1 安装器源码事实

1. 连接参数只有 `--url`、`--api-key`、可选 `--account` / `--user`；没有 `--role`、`--admin` 或 `--root-api-key` 参数。[installer L237-L267](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L237-L267)、[参数解析 L287-L307](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L287-L307)。
2. 写入 `ovcli.conf` 的连接字段是 `url`、`api_key`、`account`、`user`，没有角色提升或用户创建步骤。[`json_merge_ovcli` L677-L699](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L677-L699)。
3. 服务检查是匿名 `/health`，随后携带所提供的 key 请求 `GET /api/v1/system/status`；2xx 即通过，401/403 才视为 key 被拒绝。[`probe_server` L1650-L1669](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1650-L1669)。
4. `/api/v1/system/status` 本身只要求已解析的请求上下文，没有 ADMIN role guard。[`openviking/server/routers/system.py` L182-L199](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/routers/system.py#L182-L199)。
5. 所审阅的安装脚本中未找到调用 `/api/v1/admin/*` 创建用户或检查角色的逻辑。

**推论（基于上述源码，不是额外官方声明）**：有效 USER key 与 ADMIN key 都能通过这项安装器身份探测；安装器没有需要 ADMIN 的步骤。认证服务器上仍须先有一个可用 key，安装器本身不是用户/key 的开通工具。

**重要反例**：安装器探测通过不等于该 key 适合 agent 数据面。ROOT 也被服务端特别允许访问 `/api/v1/system/status`，所以 root key 可能通过安装器探测，却仍会在 tenant API 和 `/mcp` 被拒绝。[ROOT allowed paths](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/auth/plugins/api_key.py#L17-L35)、[ROOT tenant-data denial](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/auth/plugins/api_key.py#L203-L217)。

### 3.2 v0.5.2.1 的安装器也没有 ADMIN 前提

v0.5.2.1 tag 中同样只有通用 `--api-key` 参数，并同样探测 `/api/v1/system/status`；未见 root/admin role 参数或管理 API 调用。[v0.5.2.1 installer L237-L251](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/install.sh#L237-L251)、[v0.5.2.1 `probe_server` L1422-L1440](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/install.sh#L1422-L1440)。

当前线上安装器的下载链路、完整参数、支持的 harness、配置写入与升级/卸载边界，另见[OpenViking 官方统一安装器静态审查](2026-11-02-openviking-official-installer.md)。

## 4. 各官方接入面需要什么 key

下表中的“普通 USER 可接入”是指**已有有效的 account/user 绑定 key 且资源 ACL 允许相应操作**，不是说 USER 能自行完成初始用户注册。

| 接入面 | 第一方资料明确规定 | 是否需要 ADMIN | 证据 |
|---|---|---|---|
| **CLI 普通命令** | `ovcli.conf` 的普通 `api_key` 是 user key；USER/ADMIN 都可做 tenant 数据操作。ROOT key 通过单独的 `root_api_key` 和 `--sudo` 用于管理命令 | **否** | [CLI via `ovcli.conf`](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L110-L125)、[`--sudo` 分离](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L127-L167) |
| **内置 `/mcp`** | API Key 模式使用 **User/Admin key**，经 `X-Api-Key` 或 Bearer 发送；root key 不属于有效 MCP 数据凭据 | **否；普通 USER 明确合格** | [MCP Authentication](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/06-mcp-integration.md#L28-L35)、[MCP client troubleshooting](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/06-mcp-clients.md#L84-L90) |
| **Claude Code 插件** | hooks 与 stdio MCP proxy 共用 `ovcli.conf`；文档明确写“With a user/admin key”，排错也要求检查 user/admin key | **否** | [Install / shared config](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/02-claude-code.md#L7-L18)、[身份说明 L102](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/02-claude-code.md#L102-L103)、[auth troubleshooting](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/02-claude-code.md#L122-L130) |
| **Codex 插件** | 与 Claude Code 共用安装器和凭据文件，hooks 使用同一数据面，工具走 MCP proxy | **否**（页面未另设角色要求，适用统一认证/MCP 规则） | [Codex install](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/04-codex.md#L7-L18)、[credentials](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/04-codex.md#L95-L105) |
| **OpenCode 插件** | 页面只要求通用 OpenViking API key；hooks 与 MCP proxy 共用凭据，MCP 部分适用 User/Admin 规则 | **否**（组合该页与 canonical MCP/Auth 文档所得） | [OpenCode requirements/install](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/10-opencode.md#L7-L20)、[credential transport](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/10-opencode.md#L117-L130) |
| **pi 扩展** | 文档明确：root key 在 `/mcp` 返回 403，应创建 **user or admin key** | **否；普通 USER 明确合格** | [pi troubleshooting L113-L115](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/11-pi.md#L104-L115) |
| **DSH 插件** | 页面只写通用 `api_key` / `OPENVIKING_API_KEY`；模型工具面使用与其他插件相同的 stdio MCP proxy | **否**（DSH 页未单独规定角色；MCP 部分适用 canonical User/Admin 规则） | [DSH manual connection](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/17-dsh.md#L20-L31)、[MCP architecture](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/17-dsh.md#L109-L117)、[credentials](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/17-dsh.md#L119-L134) |
| **Hermes 插件** | 高级配置明确同时接受 User 和 Admin API keys，并由 key 推导 account/user；示例配置写 `<user key>` | **否** | [Hermes connection settings L92-L109](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/examples/hermes-plugin/CONFIGURATION.md#L92-L109)、[example L125-L133](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/examples/hermes-plugin/CONFIGURATION.md#L125-L133) |
| **Agent Plugins 1.0 / stdio MCP proxy** | 文档直接写“Use a User/Admin key for `/mcp`”；即使 legacy fallback 读到 root key，也不会使其成为有效 MCP key | **否** | [Agent Plugins credential resolution](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/15-agent-plugins.md#L37-L60) |

官方还明确支持多个 agents 共用一个 User key 共享该用户空间；只有个人数据需要分离时才使用不同 users。这是数据归属设计，不是 ADMIN 要求。[User 指南 L1-L5](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/getting-started/19-users.md#L1-L5)。

## 5. 哪些操作才需要 ROOT 或 ADMIN

### 5.1 当前 main（2026-11-02 所查）

| 操作 | 所需角色/权限 | 第一方证据 |
|---|---|---|
| 创建、列出、删除 account/workspace | **ROOT only** | [Authentication → Admin API Reference](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L640-L651)；源码 [`admin.py` L458-L558](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/routers/admin.py#L458-L558) |
| 注册/删除用户、变更 USER/ADMIN role | **ROOT 或本账户 ADMIN**；普通 USER 不可 | [Admin API roles table](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/api/08-admin.md#L11-L29) |
| 管理 groups/membership、账户配置和 memory templates | **ROOT 或本账户 ADMIN** | [Admin API roles table](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/api/08-admin.md#L19-L29)、[memory templates](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/api/08-admin.md#L222-L233) |
| 轮换他人的 key | **ROOT 或本账户 ADMIN** | [Admin API Reference](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L647-L651) |
| 轮换自己的 key | 当前 main 允许 **USER 使用 personal API-key authentication 自轮换**；也可用 ROOT/ADMIN | [同上](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L647-L651)、[`regenerate_key` 实现](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/openviking/server/routers/admin.py#L1204-L1229) |
| 列出用户 | 当前 main 允许 ROOT/ADMIN/USER；非 ROOT 限本账户，USER 的凭据可见性另有限制 | [Admin API Reference](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L647-L651)、[MCP user/credential visibility](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/06-mcp-integration.md#L214-L223) |
| CLI 的 root 管理/系统操作 | `ov --sudo ...` 使用 `root_api_key`；仅支持管理/系统类命令，不支持普通数据命令 | [CLI `--sudo`](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/api/08-admin.md#L30-L56) |
| 记忆、会话、检索、读写个人/授权共享资源 | **不要求 ADMIN**；USER/ADMIN 均可，另受资源 ACL 约束 | [Roles and Permissions](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L620-L630) |

所以，对假设中的 `meteor`：**初始注册 `meteor` 属于管理面，需要 ROOT 或本账户 ADMIN；这只是开通前提，不构成把日常 agent key 提升为 ADMIN 的理由。**

### 5.2 v0.5.2.1 与当前 main 的权限差异

核心接入结论在 v0.5.2.1 已相同：USER/ADMIN key 做 tenant 数据访问，ROOT key 不做；MCP 使用 User/Admin key。[v0.5.2.1 Authentication L46-L52](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/04-authentication.md#L46-L52)、[v0.5.2.1 Tenant Data Access L156-L165](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/04-authentication.md#L156-L165)、[v0.5.2.1 MCP Authentication L28-L35](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/06-mcp-integration.md#L28-L35)。

但有以下不能忽略的版本差异：

| 项目 | v0.5.2.1（当前部署版本） | 当前 main |
|---|---|---|
| USER 列出用户 | **不允许**；`GET .../users` 仅 ROOT/ADMIN | 允许 USER 限本账户查询 |
| USER 轮换自己的 key | **不允许**；`POST .../users/{uid}/key` 仅 ROOT/ADMIN | 允许 USER 以 personal API-key auth 轮换自己的 key |
| ADMIN 列用户凭据（默认 `include_credentials=true`） | **ROOT/ADMIN 均得本账户所有 user 明文 key**；`new.py` 的 `list_users_page` 不掩码 | ROOT 明文；ADMIN 只取自己的 key + 他人掩码前缀 |
| ADMIN 轮换他人 key | 允许（ROOT/ADMIN，限本账户），响应含新 key | 相同 |
| 日常 tenant 数据与 MCP | USER/ADMIN 均可，ROOT 不可 | 相同 |

v0.5.2.1 证据：[Admin API Reference L642-L649](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/04-authentication.md#L642-L649)、[`list_users` role guard L1008-L1014](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/routers/admin.py#L1008-L1014)、[`regenerate_key` role guard L1178-L1184](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/routers/admin.py#L1178-L1184)。当前 main 对照见 §5.1。

**对当前部署的实际含义**：不要把 main 的“USER 自轮换/列用户”能力倒推给 v0.5.2.1。`meteor` 保持 USER 不妨碍日常 agent 使用，但在 v0.5.2.1 上需要轮换其 key 时，仍要由 ROOT/ADMIN 完成。

## 6. 对三个必答问题的直接回答

1. **官方 agent 日常接入是否需要 ADMIN？**  
   **不需要。** API Key 模式下普通 USER key 是官方明确支持的数据面和 MCP 凭据；ADMIN 增加管理能力。ROOT key 不是日常 agent 数据 key。**但 v0.5.2.1 的 ADMIN/ROOT 可列用户取得明文 key 并轮换他人 key（§2.2/§5.2），其 key 仍是账户内数据等价的高权限凭据，不能与普通 USER key 同等存放。**

2. **哪些操作才需要 ROOT/ADMIN？**  
   ROOT 用于 account/workspace 生命周期及部署/系统级管理；ROOT 或本账户 ADMIN 用于注册/删除用户、变更角色、groups、账户配置及轮换他人 key。**在当前部署 v0.5.2.1 上，轮换 `meteor` 自己的 key 也需要 ROOT/ADMIN**；当前 main 已新增 USER 自轮换和限本账户列用户，不能混入旧版权限判断。**v0.5.2.1 的 ROOT/ADMIN 还能列用户并取得本账户所有 user 明文 key（默认 `include_credentials=true`），因此它们是账户内数据的等价高权限凭据，见 §2.2/§5.2。** 完整边界见 §5。

3. **`meteor` 保持 `role=user` 是否会阻碍官方安装器或工具接入？**  
   **不会。** 这是对官方安装器、CLI、MCP 和上述工具文档/源码的结论；前提是已经由管理面开通 `meteor` 并取得有效 USER key，且目标资源 ACL 允许操作。安装器没有 ADMIN 检查，普通数据与 MCP 明确接受 USER。未找到任何所核查第一方资料要求“个人日常 agent 用户必须 ADMIN”。

## 7. 例外与证据限制

- **Trusted mode 是不同认证模型**：客户端/网关可持部署 root key，同时发送 account/user identity headers；这不是要求 `meteor` 的个人角色为 ADMIN。Hermes 文档也明确把 root key + identity headers 限定为 trusted 部署。[Authentication modes](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/04-authentication.md#L7-L21)、[Hermes trusted-mode note](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/examples/hermes-plugin/CONFIGURATION.md#L106-L109)。Dev mode 不需要 key，但只适合 loopback 本地开发，不能用来证明生产接入权限。[MCP Authentication](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/guides/06-mcp-integration.md#L28-L35)。
- 本笔记以**静态第一方资料核查**为主；仅对 `state/data/viking/default/_system/users.json` 做过只读存储形态核对，未对本机 v0.5.2.1 发请求，因此“不会阻碍”有官方文档与源码支持，但不是一次本机安装验收结果。
- OpenCode、Codex、DSH 的各自页面使用泛称“API key”，未在每个页面重复写“USER role 足够”；表中已标出这是结合其官方架构与 canonical Authentication/MCP 规则所得，而非虚构每页都有明确角色字样。
- 未逐一审计所有社区插件、所有云厂商托管控制台或 OIDC/LDAP 映射；结论范围是 §范围中列出的官方自托管接入面。官方集成总入口本身也统一指向 Authentication 作为远程 API key 前提。[Agent integrations overview L36-L38](https://github.com/volcengine/OpenViking/blob/de6a440390f169be83e29b62d26b647eadd86a53/docs/en/agent-integrations/01-overview.md#L36-L38)。
- main 和线上安装器会持续变化；可点击源码链接均固定到本次记录的 commit，动态 URL 的内容以 **2026-11-02** 访问结果为准。
