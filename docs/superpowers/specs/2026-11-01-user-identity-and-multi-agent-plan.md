# OpenViking 多身份隔离与多 Agent 工具接入方案

> **状态:已定稿,待用户批准。未执行。**
>
> **执行禁令:本方案的任何步骤,在用户明确表示同意并主动通知之前,任何会话(包括
> Claude/自动化脚本)一律不得执行——不得创建 user、不得写入/分发 key、不得改动部署
> 或测试脚本。发现本文档不等于获得执行授权。(2026-11-01 用户明确要求)**

日期:2026-11-01
最近修订:2026-11-02(官方 agent 接入角色、key 权限与安装器复核)
背景:OpenViking v0.5.2.1 从零重建完成(提交 f4c833a)后,围绕"未来多个 agent
工具如何接入"与"本项目测试验证如何与真实数据隔离"的讨论定稿。

## 1. 目标与诉求

1. 未来多个 agent 工具(claude code、opencode、pi、deepseek-harness、
   hermes-agent 等)接入本部署;
2. 数据隔离:工具间不要求互相隔离;**个人偏好/个人信息须跨工具共享**;
   项目经验按项目组织;
3. **本项目部署自身的测试验证/功能实验与真实使用数据完全分离**,测试残留
   不得影响任何真实身份的数据,不得误覆盖他 user(含 default)的数据;
4. 遵循官方机制,不自造轮子。

## 2. 官方机制取证结论(v0.5.2.1 镜像内源码 + docs.openviking.ai,2026-11-01)

### 2.1 命名空间与隔离模型

- 三级身份:account(完全隔离)→ user(同账户互不可见)→ peer(请求级视图过滤)。
- user 私有:`viking://user/<uid>/{memories,resources,skills,sessions,peers,privacy}`;
  同账户**非管理员 user 之间**互不可见(不可读),故私有数据无被其他普通 user
  覆盖的路径。账户 ADMIN/ROOT 可通过列举或轮换 user key 间接取得访问(见 §2.4/§4),
  这不是普通 user 间的隔离面。
- 账户共享:`viking://resources`(顶层)与 `viking://agent/skills`——**任何 user
  可见可写**,是跨 user 的唯一共享面;`--to` 指向已存在 URI 时为**替换语义**。
- peer(`X-OpenViking-Actor-Peer` 请求头)只过滤 memories/resources 两段;
  sessions 不按 peer 隔离。记忆路由:role=user 的消息带 peer_id 时写
  `peers/<peer_id>/memories`,但用户自身事实(偏好/实体)仍提取到 user 根。
- Role(ROOT/ADMIN/USER)只影响管理权,不影响数据可见性;数据边界由命名空间决定。
- root key 在数据面与 `/mcp` 均被拒(实测 401/403),agent 接入必须用 user key。

### 2.2 官方 agent 接入体系(用户列举的工具全部有官方集成)

| 工具 | 官方集成 | 备注 |
|---|---|---|
| Claude Code | 官方 plugin(9 hooks + MCP proxy + 4 skills),marketplace `openviking-memory@openviking` | 源码 `examples/claude-code-memory-plugin` |
| OpenCode | npm 插件 `@openviking/opencode-plugin` | |
| pi | 官方原生扩展 | `examples/pi-coding-agent-extension` |
| deepseek-harness (DSH) | in-process Cordis 插件 `@openviking/dsh-memory-plugin` | 官方默认 `OPENVIKING_RECALL_PEER_SCOPE=all`(不带 actor-peer 头);MCP proxy 每 profile 一次,自动召回/捕获仍按会话工作区 peer;不是默认的进程级 actor peer |
| hermes-agent | 官方 catalog 插件 | `examples/hermes-plugin` |
| 任意 MCP 客户端 | 服务器内置 `/mcp`(streamable HTTP;**当前部署 v0.5.2.1 实测 24 工具**,含 find/search/read/write/remember/forget/add_skill/list_users/list_groups/get_acl/set_acl/privacy_* 等) | `openviking/server/mcp_endpoint.py` |

- 统一安装器:`curl -fsSL https://openviking.ai/install | bash`;凭据写宿主
  `~/.openviking/ovcli.conf`(JSON:服务器地址 + user api_key),官方插件均从该
  文件取连接信息;可用 `OPENVIKING_*` 环境变量按进程覆盖。当前线上安装器的
  下载链路、完整参数、支持的 harness、配置合并/备份及重跑/卸载边界见
  [OpenViking 官方统一安装器静态审查](../../research/2026-11-02-openviking-official-installer.md)。
  统一安装器的 `--harness` 不含 Hermes、OpenClaw、Agent Plugins 1.0;这些工具
  须按各自官方接入方式安装,不能假设同一条安装命令覆盖全部官方集成。
- 插件生命周期全自动:prompt 前自动召回注入、每轮捕获、20K pending 自动 commit、
  退出/压缩前 commit、离线队列重放。
- **官方 peer 轴 = 工作区/仓库维度**(默认由 git origin URL 派生,同一项目跨
  clone/工具共享同一 peer 记忆;仓库外不发送 peer → 写 user 级),**不是按工具**。
  需要例外时用 `OPENVIKING_PEER_SOURCE` / `.openviking/config.json` 单独覆盖。
- 会话 ID 有官方前缀约定(cc-/oc-/pi-/dsh-/cx-…),来源工具天然可区分。
- VikingBot Gateway(18790)定位是"无插件客户端"的模型代理备选;本项目不启用,
  用户工具均有官方插件,用不上。

### 2.3 user 语义(官方判据)

官方 User 文档:"A User is a data-owning identity... An application can map it
to a person **or a separate application identity**. Multiple agents using one
User key share that user's space; **use different users when their personal
data needs to remain separate**."——即 user 按"数据是否需要彼此分离"划分;
官方集成生态本身默认单 user 单 key 多工具。

### 2.4 角色与 key 权限(2026-11-02 官方复核)

- API Key 模式下,USER 与 ADMIN key 均可用于 tenant 数据面和 `/mcp`;普通操作
  仍受命名空间与资源 ACL 约束。ROOT key 用于账户/部署管理,不能作为 agent
  数据面 key。
- 官方统一安装器只接收通用 `--api-key` 与可选 account/user,不调用 Admin API
  开通用户,也不检查或提升角色。已有有效 USER key 时,官方安装器、CLI、MCP
  及本方案所列 agent 插件均不要求 ADMIN。安装器的 `/health` 与
  `/api/v1/system/status` 探测不是数据面验收:ROOT 可能通过探测,却仍会被
  数据面/`/mcp` 拒收。
- account/workspace 生命周期仅 ROOT;注册/删除 user、变更 role、groups、账户
  配置及轮换他人 key 由 ROOT 或本账户 ADMIN 执行。当前部署 v0.5.2.1 中,USER
  不能列 user 或轮换自己的 key,这两项也需 ROOT/ADMIN;当前 main 已允许的 USER
  限本账户列 user 与自轮换能力不得倒推到本部署。
- ADMIN 增加管理能力;**在 v0.5.2.1 上,ADMIN 还可通过
  `GET /api/v1/admin/accounts/<account>/users`(默认 `include_credentials=true`)
  取得本账户所有 user 的明文 key,并可通过 `POST .../users/<uid>/key` 轮换任何
  user 的 key**;因此 ADMIN key 在效果上等价于其账户内全部 user 数据的读取/覆盖
  能力。ROOT 同样可通过该端点或轮换路径取得 user key(只是不能直接访问 tenant
  数据)。用户级隔离只对非管理员 user 成立,不能抵御 default/root key 泄露。
  个人 agent 日常接入仍不需要 ADMIN;default/root key 必须按 crown-jewel 级
  保护,不得放进 agent 配置或测试夹具。
- 版本差异陷阱:当前 main 已改为 ROOT 取明文、ADMIN 只取自己的 key + 他人掩码
  前缀;不能把 main 的掩码行为倒推到 v0.5.2.1(与上一条 USER 能力同属"不得倒推"
  的差异,方向相反)。

完整第一方证据与固定版本链接见
[OpenViking 官方 agent 接入是否需要 ADMIN](../../research/2026-11-02-openviking-agent-auth-roles.md)。

## 3. 方案:三身份格局(核心决策)

| 身份 | user_id | role | key 位置 | 用途 |
|---|---|---|---|---|
| 部署运维 | `default`(现有) | admin | 容器 `state/secrets/user_api_key` + `state/ovcli.conf`(现状不变) | bootstrap、doctor/smoke、部署验收、运维管理 |
| 正式个人 | `meteor`(新建) | user | 宿主 `~/.openviking/ovcli.conf`(官方安装器写入) | 所有 agent 插件、真实记忆/资源/偏好 |
| 测试 | `test`(新建) | user | `state/secrets/test_user_api_key`(600,独立文件) | 本项目测试验证、功能实验 |

- `meteor` 的 role 已定为 `user`(不再开放):官方 agent 数据面/MCP 与统一
  安装器均不要求 ADMIN;管理操作统一由 default/root 完成,不得为了日常 agent
  接入提升 `meteor` 为 ADMIN。
- 现有 8 个文件位于**账户共享区 `viking://resources`**(`deployment-smoke*`
  冒烟资源 + 该根目录的 `.abstract`/`.overview` 元数据;另有 `agent/`、
  `user/default/` 的系统骨架),不是 default 的 user 私有数据;它们对同账户
  所有 user(含 meteor/test)可见。保留在共享区作运维冒烟可以,但不能称为
  "default 私有沙箱"。
- 真实数据(个人偏好、项目记忆、知识库)在 `meteor` 的 user 私有区
  (`viking://user/meteor/...`)从零积累;账户共享区不是"干净"的。
- 测试残留**在 test 私有区之内**对其他普通 user 不可见;但 §5.2 允许的共享区
  写入、现有共享冒烟内容、以及 default/root 的 key 权限都不受 user 隔离保护
  (§5.4 已接受这些账户级残留)。不得把"test 私有"表述为"对任何身份零影响"。
  test 亦可用作未来多 user 交互测试(ACL/可见性边界)的对端素材。
- agent 工具不逐工具建 user/key(官方生态为单 key 多工具;个人记忆本就要求
  跨工具共享,与逐工具 user 矛盾);接受官方默认 peer 轴(仓库维度),个别
  工具确需独立记忆时单独覆盖其 peer 配置。

## 4. key 与凭据布局(汇总)

| 凭据 | 位置 | 权限 | 说明 |
|---|---|---|---|
| root key | `secrets.env` + default 的 `state/ovcli.conf(.local)`(现有) | 600 | 仅账户管理;数据面/`/mcp` 拒收;bootstrap 会将 root key 一并写入 default CLI 配置;**可用 Admin API 列举/轮换 user key,故仍等效于账户内数据的间接访问** |
| default user key | `state/secrets/user_api_key` + `state/ovcli.conf(.local)`(现有) | 600 | 部署运维身份(**role=admin**;v0.5.2.1 可列举本账户 user 明文 key/轮换他人 key,见 §2.4);CLI 配置同时含 root key |
| meteor user key | 宿主 `~/.openviking/ovcli.conf`(未来,官方安装器) | 600 | 正式身份;宿主侧文件;**服务端 users.json 另存明文副本(见下)** |
| test user key | `state/secrets/test_user_api_key` + 选用 CLI 配置时的 `state/ovcli.test.conf`(均新建) | 600 | 测试身份;两处如同时存在须同步轮换;**服务端 users.json 另存明文副本(见下)** |

- **服务端 key 存储(2026-11-02 实测)**:OpenViking 把每个 user 的 API key
  明文写入 `state/data/viking/default/_system/users.json`(当前文件 644;外层
  `state` 为 700,故受目录权限保护)。该文件在本 repo `state/` 内,随 README
  备份一起打包;`state/secrets/*` 与 CLI 配置不是唯一副本。Admin API 创建
  `test`/`meteor` 后,其 key 会立即出现在此文件中;轮换 API 会更新它。若需
  避免明文落盘,须在创建 user 前确认/启用 API key hashing(本部署当前未启用)。

凭据纪律(沿用本项目既有要求):永不回显明文、不进 git、命令内变量中转、
轮换时同步处理 default 的 `ovcli.conf(.local)` 与 test 的 key 文件/可选
`ovcli.test.conf`;并注意上述 users.json 副本会随 `state/` 进入备份。

## 5. 测试纪律(方案执行后生效)

1. 凡会写数据或验证用户数据面的功能测试一律以 `test` 身份执行;测试脚本/CLI
   配置切换方式执行时定一种(独立 `state/ovcli.test.conf`(600)经
   `OPENVIKING_CLI_CONFIG_FILE` 切换,或脚本内 curl 直连带 test key)。
   部署 doctor/smoke 与认证矩阵中明确需要 default/root 的部署验收、认证边界
   检查是例外,但不得写真实使用数据。
   若新建 `state/ovcli.test.conf`,只放 test user key,**不得**复制 default
   配置中的 `root_api_key`(用 `ov config add` 时不要传 `--root-api-key-env`);
   该文件给测试进程 root 能力会破坏测试身份边界。现有
   `tests/e2e_auth_matrix.sh` 的 `ADMIN_KEY` 实际是 default 的 **ADMIN** key
   (见 §3),不是 role=user;脚本已明确标注其只验证 ADMIN/ROOT 边界,且密钥经
   curl stdin 配置传入、不进 argv。创建 test 后应再用 test key 补测普通 USER
   边界。
2. 写入目标优先 test 私有区(`viking://user/test/resources/...`;执行时先实测
   `ov add-resource` 对该路径的支持);**仅验证共享语义时才写**
   `viking://resources`,且专用 URI + 验收后即删(沿用 2026-10-26 惯例)。
3. 不写记忆;若未来专门测试记忆管线,只写 test 私有区。
4. 已知不受身份隔离保护的残留(接受):账户共享区内容、GLM token 消耗
   (Coding Plan 用量)、向量库存储、任务队列均为账户/服务器级。

## 6. 执行步骤(仅用户批准后)

1. root key 调 `POST /api/v1/admin/accounts/default/users` 创建 `test`
   (role=user);响应 `user_key` 原子写入 `state/secrets/test_user_api_key`(600)。
   本步不创建 `meteor`,只为缩小首个审批的影响面;**注意:创建任何 user 时,
   服务端都会把其 key 明文写入 `state/data/.../_system/users.json`(见 §4),
   因此"不落入 repo/备份"不能靠推迟创建实现**,只能靠 key hashing/外部账户
   或接受该副本。
2. 为 test 建立第 5.1 条选定的 CLI 使用方式;`ov health -o json` 分别以 default 与
   test 身份验证,除 `healthy=true` 外必须断言 `result.account_id=default`,且
   `result.user_id` 分别为 `default`/`test`(meteor 在宿主侧接入时同样核对身份)。
3. 双向验证:test 写私有区后 default 不可见,test 也不可读取 default 既有
   私有资源。**注意 default 的 user 私有区当前只有 `.abstract`/`.overview`
   骨架,没有可测资源;现有 8 个冒烟文件在共享区 `viking://resources`,test
   可以读取。执行前须先在 `viking://user/default/resources/` 放一个专用
   私有夹具,再用 test 验证不可读;不要拿共享区冒烟资源当"default 私有资源"。**
   `ov ls viking://resources` 确认共享区无新增残留(基线包含既有
   `deployment-smoke*`,不是空)。
4. README 增补三身份表、key 位置与测试纪律;引用本方案文档。
5. (独立后续,另行批准)root key 创建 `meteor`(role=user),双向验证其与 test
   均不可读取对方私有区;宿主侧按官方接入方式安装各 agent 工具(统一安装器
   仅覆盖其支持的 harness,Hermes 等走各自官方流程),meteor key 通过交互无回显
   输入或既有 600 配置提供,写入宿主 `~/.openviking/ovcli.conf`(600),不在
   repo `state/secrets/` 另存副本。**但服务端 users.json 仍会保存该 key 的明文
   副本(§4),因此"只写入宿主"不成立;备份/迁移 `state/` 即包含它。**

任一 user 创建成功但 key 本地落盘失败时,不得重复创建 user;用 root key 调
`POST /api/v1/admin/accounts/default/users/<uid>/key` 轮换,再将返回的新 key
原子写入上述对应位置(600)并重跑身份与双向隔离验证。

## 7. 开放点(批准前可调整)

- user 命名(`meteor` / `test` 为当前拟定);
- 部署设计 §9.4 与 README 的端到端验收命令是否改写到 test 私有区(依赖第 6.2 条实测)。

## 8. 方案修正记录(避免回退)

- 2026-11-02 官方接入复核关闭 `meteor` 是否需要 admin 的开放点:普通 USER
  key 已满足官方数据面、MCP、CLI 与安装器接入;当前部署 v0.5.2.1 的 key 轮换
  仍走 ROOT/ADMIN 管理路径,详见 §2.4。
- 早期方案曾提议"每工具一个 peer(claude-code/opencode/pi…)"与"每工具独立
  user key"(选项 A/B/C)——**已被官方证据否决**:官方 peer 轴是仓库维度而非
  工具维度;官方插件生态为单 user key 共享。未来讨论不得回到该版本。
- 早期"会话自定命名区分工具"亦多余:官方会话 ID 前缀已天然区分来源。
