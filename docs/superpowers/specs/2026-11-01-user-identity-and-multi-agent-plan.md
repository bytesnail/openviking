# OpenViking 多身份隔离与多 Agent 工具接入方案

> **状态:已定稿,待用户批准。未执行。**
>
> **执行禁令:本方案的任何步骤,在用户明确表示同意并主动通知之前,任何会话(包括
> Claude/自动化脚本)一律不得执行——不得创建 user、不得写入/分发 key、不得改动部署
> 或测试脚本。发现本文档不等于获得执行授权。(2026-11-01 用户明确要求)**

日期:2026-11-01
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
  同账户 user 之间**互不可见(不可读)**,故私有数据无被他人覆盖的路径。
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
| deepseek-harness (DSH) | in-process Cordis 插件 `@openviking/dsh-memory-plugin` | DSH 为进程级 actor peer(特例) |
| hermes-agent | 官方 catalog 插件 | `examples/hermes-plugin` |
| 任意 MCP 客户端 | 服务器内置 `/mcp`(streamable HTTP,21 工具:find/search/read/write/remember/forget/add_skill 等) | `server/mcp_endpoint.py` |

- 统一安装器:`curl -fsSL https://openviking.ai/install | bash`;凭据写宿主
  `~/.openviking/ovcli.conf`(JSON:服务器地址 + user api_key),官方插件均从该
  文件取连接信息;可用 `OPENVIKING_*` 环境变量按进程覆盖。
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

## 3. 方案:三身份格局(核心决策)

| 身份 | user_id | role | key 位置 | 用途 |
|---|---|---|---|---|
| 部署运维 | `default`(现有) | admin | 容器 `state/secrets/user_api_key` + `state/ovcli.conf`(现状不变) | bootstrap、doctor/smoke、部署验收、运维管理 |
| 正式个人 | `meteor`(新建) | user | 宿主 `~/.openviking/ovcli.conf`(官方安装器写入) | 所有 agent 插件、真实记忆/资源/偏好 |
| 测试 | `test`(新建) | user | `state/secrets/test_user_api_key`(600,独立文件) | 本项目测试验证、功能实验 |

- 现有 default 下 8 文件/2 技能/2 记忆均为部署验收冒烟 + 系统首启骨架,
  留在 default 作运维沙箱,**无需清理**。
- 真实数据(个人偏好、项目记忆、知识库)从零在 `meteor` 下干净积累。
- 测试残留全部落在 test 私有空间(user 级互不可见),对任何身份零影响;
  test 亦可用作未来多 user 交互测试(ACL/可见性边界)的对端素材。
- agent 工具不逐工具建 user/key(官方生态为单 key 多工具;个人记忆本就要求
  跨工具共享,与逐工具 user 矛盾);接受官方默认 peer 轴(仓库维度),个别
  工具确需独立记忆时单独覆盖其 peer 配置。

## 4. key 与凭据布局(汇总)

| 凭据 | 位置 | 权限 | 说明 |
|---|---|---|---|
| root key | `secrets.env`(现有) | 600 | 仅账户管理;数据面/`/mcp` 拒收 |
| default user key | `state/secrets/user_api_key` + `state/ovcli.conf(.local)`(现有) | 600 | 部署运维身份 |
| meteor user key | 宿主 `~/.openviking/ovcli.conf`(未来,官方安装器) | 600 | 正式身份;宿主侧文件,不属于本 repo |
| test user key | `state/secrets/test_user_api_key`(新建) | 600 | 测试身份 |

凭据纪律(沿用本项目既有要求):永不回显明文、不进 git、命令内变量中转、
轮换时同步处理 `ovcli.conf(.local)` 两份。

## 5. 测试纪律(方案执行后生效)

1. 测试一律以 `test` 身份执行;测试脚本/CLI 配置切换方式执行时定一种
   (独立 `state/ovcli.test.conf` 经 `OPENVIKING_CLI_CONFIG_FILE` 切换,或
   脚本内 curl 直连带 test key)。
2. 写入目标优先 test 私有区(`viking://user/test/resources/...`;执行时先实测
   `ov add-resource` 对该路径的支持);**仅验证共享语义时才写**
   `viking://resources`,且专用 URI + 验收后即删(沿用 2026-10-26 惯例)。
3. 不写记忆;若未来专门测试记忆管线,只写 test 私有区。
4. 已知不受身份隔离保护的残留(接受):账户共享区内容、GLM token 消耗
   (Coding Plan 用量)、向量库存储、任务队列均为账户/服务器级。

## 6. 执行步骤(仅用户批准后)

1. root key 调 `POST /api/v1/admin/accounts/default/users` 创建 `meteor`、
   `test`(均 role=user);响应 `user_key` 原子写入 `state/secrets/`(600)。
2. 为 test 建立第 5.1 条选定的 CLI 使用方式;`ov health` 分别以 default 与
   test 身份验证通过(meteor key 在宿主侧接入时由安装器验证)。
3. 验证:test 写私有区后 default/meteor 均不可见;`ov ls viking://resources`
   确认共享区无新增残留。
4. README 增补三身份表、key 位置与测试纪律;引用本方案文档。
5. (独立后续,另行批准)宿主侧官方安装器接入各 agent 工具,写入
   `~/.openviking/ovcli.conf` 用 meteor key。

## 7. 开放点(批准前可调整)

- user 命名(`meteor` / `test` 为当前拟定);
- §9.4 端到端验收命令是否改写到 test 私有区(依赖第 6.2 条实测);
- `meteor` 是否需要 admin(当前定 user;需要管理操作时用 default/root)。

## 8. 方案修正记录(避免回退)

- 早期方案曾提议"每工具一个 peer(claude-code/opencode/pi…)"与"每工具独立
  user key"(选项 A/B/C)——**已被官方证据否决**:官方 peer 轴是仓库维度而非
  工具维度;官方插件生态为单 user key 共享。未来讨论不得回到该版本。
- 早期"会话自定命名区分工具"亦多余:官方会话 ID 前缀已天然区分来源。
