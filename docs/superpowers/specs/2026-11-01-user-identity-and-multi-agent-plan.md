# OpenViking 多身份隔离与多 Agent 工具接入方案

> **状态:已获用户当次明确批准并完成实施(2026-11-04)。**
>
> **授权记录:用户在 2026-11-04 会话明确指示"开始执行",随后审阅并选择
> 本会话直接执行实施计划
> `docs/superpowers/plans/2026-11-04-user-identity-and-two-agents.md`。
> 本次 Agent 范围仅 Claude Code 与 OpenCode 2.x;原"待批准、禁止执行"状态
> 已由该当次授权解除,不代表允许超出上述计划的操作。**

日期:2026-11-01
最近修订:2026-11-02(官方 agent 接入角色、key 权限与安装器复核)
背景:OpenViking v0.5.2.1 从零重建完成(提交 f4c833a)后,围绕"未来多个 agent
工具如何接入"与"本项目测试验证如何与真实数据隔离"的讨论定稿。

## 1. 目标与诉求

1. 未来多个 agent 工具(claude code、opencode、pi、deepseek-harness、
   hermes-agent 等)接入本部署;
2. 数据隔离:工具间不要求互相隔离;**个人偏好/个人信息须跨工具共享**;
   项目经验按项目组织;
3. **原始诉求是测试验证/功能实验与真实使用数据完全分离**,测试残留不得影响
   任何真实身份的数据,不得误覆盖他 user(含 default)的数据。经本次复核,
   同账户三身份格局只能实现**协作式 user 私有区隔离**,不能满足这一绝对目标:
   共享区写入、同 UID 凭据可达性及账户/服务器级残留仍在(§5.4/§5.5)。
   本方案只验收前者,不得宣称完成绝对的"完全分离";若该原始诉求不可放宽,
   必须另案采用独立测试账户(至少)、独立服务实例及仅含 test key 的独立 OS
   用户/容器,而不是靠路径约定模拟边界。
4. 遵循官方机制,不自造轮子。

## 2. 官方机制取证结论(v0.5.2.1 镜像内源码 + docs.openviking.ai,2026-11-01)

> 取证说明(2026-11-03 检视补充):§2.1/§2.2 中标注"实测"的行为(/mcp
> 工具清单与"24 工具"计数、root key 数据面 401/403、`_is_accessible()` 的
> ROOT 放行路径、插件 hooks/skills 构成、市场与包名)来自 2026-11-01 容器内
> 会话实测,当时未单独落盘报告;已落盘的复核文档为
> docs/research/2026-10-25-* 与 2026-11-* 两批(后者覆盖 §2.4 与统一安装器)。
> 引用"24 工具"等具体数字时须以当次 `/mcp` tools/list 实测为准,服务端
> 升级后不得沿用旧值。

### 2.1 命名空间与隔离模型

- 三级身份:account(完全隔离)→ user(同账户普通 user 互不可见)→ peer(请求级视图过滤)。
- user 私有:`viking://user/<uid>/{memories,resources,skills,sessions,peers,privacy}`;
  同账户**非管理员 user 之间**互不可见(不可读),故私有数据无被其他普通 user
  覆盖的路径。账户 ADMIN/ROOT 可通过列举或轮换 user key 间接取得访问(见 §2.4/§4),
  这不是普通 user 间的隔离面。
- 账户共享:`viking://resources`(顶层)与 **整个 `viking://agent` 命名空间**
  (含 `agent/skills`、`agent/endpoints`、`agent/payments`、`agent/tools` 等;
  服务端按命名空间对同账户 user 放行)——**任何 user 可见可写**,是跨 user 的
  共享面;`--to` 指向已存在 URI 时为**替换语义**。
- peer(`X-OpenViking-Actor-Peer` 请求头)只过滤 memories/resources 两段;
  sessions 不按 peer 隔离。记忆路由:role=user 的消息带 peer_id 时写
  `peers/<peer_id>/memories`,但用户自身事实(偏好/实体)仍提取到 user 根。
- Role(ROOT/ADMIN/USER)本身不改变数据面的命名空间可见性;但 ADMIN/ROOT 的管理
  权限可经 §2.4 的凭据列举/轮换间接取得账户内数据访问,且服务端
  `_is_accessible()` 对 ROOT 角色存在整树放行路径,故不能把 ADMIN/ROOT key
  当作"只能管理、不能读数据"的低风险身份。
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

- 统一安装器:先在新空目录 `install_dir=$(mktemp -d)` 中用
  `curl -fsSL https://openviking.ai/install -o "$install_dir/bootstrap.sh"`
  下载,成功后再以 `bash "$install_dir/bootstrap.sh" ARGS...` 执行;
  **不直接 `curl | bash`**(避免下载失败被 bash 的 0 状态掩盖或执行截断脚本)。
  凭据写宿主 `~/.openviking/ovcli.conf`(JSON:服务器地址 + user api_key),
  官方插件均从该文件取连接信息;可用 `OPENVIKING_*` 环境变量按进程覆盖。
  当前线上安装器的下载链路、完整参数、支持的 harness、配置合并/备份及
  重跑/卸载边界见
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
  (前两类属 §5.4 的已接受残留;default/root key 权限不属该清单,见 §2.4 的
  crown-jewel 要求)。不得把"test 私有"表述为"对任何身份零影响"。
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
| test user key | `state/secrets/test_user_api_key`(独立文件) | 600 | 本项目测试验证、功能实验;默认 direct-curl 使用,不创建持久 test CLI active/saved profile;**服务端 users.json 另存明文副本(见下)** |

- **服务端 key 存储(2026-11-02 只读核对存储形态,未记录/回显值)**:OpenViking
  把每个 user 的 API key 明文写入 `state/data/viking/default/_system/users.json`
  (当前文件 644;外层 `state` 为 700,**只对同机其他 OS 用户生效**;同 UID 1000
  的 agent/测试进程仍可读,不能当作隔离边界)。该文件在本 repo `state/` 内,随
  README 备份一起打包;`state/secrets/*` 与 CLI 配置不是唯一副本。Admin API
  创建 `test`/`meteor` 后,其 key 会立即出现在此文件中;轮换 API 会更新它。
  若需避免明文落盘,须在创建 user 前确认/启用 API key hashing(本部署当前未启用)。

凭据纪律(沿用本项目既有要求):永不回显明文、不进 git、命令内变量中转;
`users.json` 与 `state/secrets/*`、CLI 配置一样禁止 cat/Read/粘贴;轮换时同步
处理 default 的 `ovcli.conf(.local)`(后者是 `ov config add --name local` 写入的
CLI saved profile,不是手工副本)、test 的 key 文件;test 默认不再维护持久
`ovcli.test.conf` / `ovcli.conf.test` 副本,避免轮换漂移。专项 CLI 测试如确需
持久配置,必须另行批准并复用 §6.2 的参数化配置/健康检查 helper 同步管理;
并注意 users.json 副本会随 `state/` 进入备份。

## 5. 测试纪律(方案执行后生效)

1. 凡会写数据或验证用户数据面的功能测试一律以 `test` 身份执行;项目脚本
   **统一使用 `state/secrets/test_user_api_key` + direct curl**,密钥必须经变量
   中转与 curl stdin 配置(`-K -`)传入,不得进 argv;curl 首参为 `-q` 禁用
   用户 curlrc,默认丢弃响应体,只输出状态/非敏感指标。不得另写裸
   `curl -H "X-API-Key: $KEY"`;新测试应复用认证矩阵的 `code_auth` 模式(密钥经
   stdin `-K -` 传入 curl)。抽取目标必须是**不含任何密钥加载逻辑的可 source
   库**(如 `tests/curl_auth.sh`),不得 source 可执行矩阵脚本本身——那会执行
   整个矩阵并把 ROOT/ADMIN key 载入新测试的环境(2026-11-03 检视补充)。
   部署 doctor/smoke 与认证矩阵中明确需要 default/root 的部署验收、认证边界
   检查是例外,但不得写真实使用数据。test 默认不创建持久 CLI active/saved
   profile,避免 key 文件之外再多两份需同步轮换的凭据;身份健康检查只用
   §6.2 helper 生成的临时 600 配置,验收后删除。专项 CLI 测试确需持久配置时
   另行批准,并参数化复用 bootstrap 的配置、saved-profile 同步与 timeout helper,
   不得手工维护一套平行流程。
   现有 `tests/e2e_auth_matrix.sh` 的 `ADMIN_KEY` 实际是 default 的 **ADMIN**
   key(见 §3),不是 role=user;当 `state/secrets/test_user_api_key` 存在时脚本
   自动追加 USER 边界行,否则 SKIP。创建 test 后以
   `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh` 重跑,缺失 USER 行必须失败;
   该矩阵只验证状态边界,不替代 §6.3 的双向私有读写隔离验收。
2. 写入目标优先 test 私有区(`viking://user/test/resources/...`;执行时先实测
   `ov add-resource` 对该路径的支持);**仅验证共享语义时才写**
   `viking://resources`,且专用 URI + 验收后即删(沿用 2026-10-26 惯例)。
   共享面还包括整个 `viking://agent` 命名空间(§2.1);测试**不写**
   `viking://agent/*`;若确需验证,单独记录 URI 并在验收后删除。
3. 不写记忆;若未来专门测试记忆管线,只写 test 私有区。
4. 已知不受身份隔离保护的残留(接受):账户共享区内容(含 `viking://agent`
   命名空间)、GLM token 消耗(Coding Plan 用量)、向量库存储、任务队列均为
   账户/服务器级。default/root key 权限不在此清单,另见 §2.4。**接受这些残留
   即表示 §1.3 的绝对"完全分离"目标未由本方案关闭**,不得再以私有区验收通过
   宣称共享区或真实身份不受任何影响。

### 5.5 隔离边界(执行前必读)

- 本方案的 user 隔离是**服务端命名空间层面的协作式隔离**,不是宿主进程的
  权限边界。agent/测试目前都以宿主 UID 1000 运行,能读取
  `state/secrets/user_api_key`、`state/data/viking/default/_system/users.json`
  与 default 的 CLI 配置,并可切换到 default/ADMIN/ROOT 身份。
- 任何会写数据的测试必须在执行前断言身份:§6.2 的参数化 helper 从 test key
  文件生成仅含 test key 的临时 600 CLI 配置,显式设置
  `OPENVIKING_CLI_CONFIG_FILE`,并在验证后删除;容器内完整命令为
  `docker compose exec -T -e OPENVIKING_CLI_CONFIG_FILE=/app/.openviking/ovcli.test.conf openviking ov health -o json`。
  必须核对输出的 `user_id=test`、`account_id=default`、`role=user`;不得依赖
  宿主环境变量、容器默认配置或不完整的 `docker compose exec -e ...` 片段。
- 临时配置的落盘位置(2026-11-02 检视补充;2026-11-03 修正):上例把配置放进
  `/app/.openviking/`(宿主 `state/`)虽可工作,但崩溃/中断会把明文 test key
  残留进 `state/`,并随其后每次 tar 备份长期扩散——恰好重新引入 §4 以
  "test 不再维护持久 CLI 副本"消除的多副本漂移。helper 应优先经
  `docker compose exec -T openviking sh -c 'cat > /tmp/ovcli.test.conf && chmod 600 ...'`
  从 stdin 把配置写进容器 `/tmp`(不进 `state/`、不进备份)。**注意:当前
  compose 的 openviking service 未配置 tmpfs(仅 embedding-adapter 有),
  其 `/tmp` 是容器 overlay 可写层——`docker compose restart` 后仍在,容器
  重建才消失(2026-11-03 检视核实 compose.yaml);"内存级随容器消失"不
  成立**,若需真正的 tmpfs 须在执行时一并给该 service 增加 tmpfs 挂载
  (属执行期变更)。无论哪种位置都必须:验收后立即删除并核对、崩溃后重跑
  前先清理残留、轮换 test key 时同步清理此类副本;`/tmp` 方案还须注意其
  残留窗口横跨 restart。退而求其次才用 `state/` 内临时文件(同样受上述
  清理纪律约束,且会随备份扩散)。
- 若需要抵御测试脚本误用或同 UID 进程读取,须把测试运行器放入独立 OS 用户
  或容器,仅挂载 test key,不挂载 default key/users.json;本方案当前未实施
  该 OS 级隔离,相关验收不得宣称"对任何身份零影响"。

## 6. 执行步骤(仅用户批准后)

1. root key 调 `POST /api/v1/admin/accounts/default/users` 创建 `test`
   (role=user);响应 `user_key` 原子写入 `state/secrets/test_user_api_key`(600)。
   实现时参数化复用 `bootstrap_auth.py` 的 `_post_json`、user 注册、
   `_atomic_write_secret` 与"创建成功但落盘失败则轮换、绝不重建"恢复逻辑,
   不另写一套 curl/原子写平行实现。(参数化需当场修改 `bootstrap_auth.py`:
   现有 `_register_default_user`、`run()` 的 409 回退链与落盘失败提示均硬编码
   default/admin,并非可直接传参的现成 helper;该修改属于本步批准范围内的
   实现工作,不违反文首执行禁令——禁令针对批准前的改动。)
   本步不创建 `meteor`,只为缩小首个审批的影响面;**注意:若 §4 的 key hashing
   未启用/未确认,创建任何 user 时服务端
   都会把其 key 明文写入 `state/data/.../_system/users.json`(见 §4),因此
   "不落入 repo/备份"不能靠推迟创建实现**,只能靠 key hashing/外部账户或接受
   该副本。
2. 参数化复用 `bootstrap_auth._ov_health_ok` 的 JSON envelope、root-fallback 与
   key/URL 一致性检查(预期 account/user/role 及 root key 是否必需必须作为参数,
   不能原样硬调 default/admin helper)。除身份断言外,还须参数化 helper 内多处
   与 default 部署绑定的硬编码(2026-11-02 检视补充,2026-11-03 补全):
   其一,`_saved_profile_path` 固定拼同目录 `ovcli.conf.local`——当临时配置与
   default 配置同目录(如 `/app/.openviking/`)时,原样复用会把 default 的
   saved profile 当作 test 的漂移检查对象而恒判失败,test 自身真正的漂移反而
   漏检;而按 §5.5 优先位置放 `/tmp` 时,该路径下不存在任何 saved profile,
   此检查对 test 临时配置无对象可查,属预期跳过(default 自身的 saved 检查
   仍由 bootstrap 幂等路径负责)——参数化 helper 必须显式接收"saved profile
   检查对象"参数,不得隐式拼路径,也不得为"可检查"而反向把临时配置改放
   `state/`;
   其二,`_cli_config_matches` 强制要求配置含与目标一致的
   `root_api_key`——§5.5 的临时配置只含 test key,且 §2.4 明令 root key 不得
   放进测试夹具,故该比较必须按身份设为可选,不能为实现"通过检查"而在临时
   配置写入 root key;
   其三,`_configure_cli`/`CLI_PROFILE_NAME` 固定 `local`——若按 §5.1 的
   "专项 CLI 测试确需持久配置"复用 config add,profile 名必须可传独立值,
   否则 `ov config add --name local` 会覆盖 default 的 saved profile
   `state/ovcli.conf.local`(README 既有警告;2026-11-03 补全);
   其四,`_cli_config_matches` 对缺 `url` 的配置静默代入
   `DEFAULT_BASE_URL`——临时配置必须显式含 `url`,该默认代入不得带入
   参数化 helper(2026-11-03 补全);
   另注意 CLI 显示语言门禁读取的是
   `$HOME/.openviking/ovcli.settings.conf`(与 `OPENVIKING_CLI_CONFIG_FILE`
   路径无关;default bootstrap 已建立该文件,2026-10-26 测试报告 F4 核实)——
   若 helper 在全新 HOME 下运行须先重建语言设置,否则 `ov health` 会在发请求
   前退出,被误读为身份不符。test 健康检查用临时 600 配置且完成后删除。
   `ov health -o json` 分别以 default 与 test 身份验证,除 `healthy=true` 外必须
   断言 `result.account_id=default`、`result.user_id` 分别为 `default`/`test`,
   且 `result.role` 为 `admin`/`user`(**`role=root` 判失败**:root-only 配置
   也会返回 default/default)(meteor 在宿主侧接入时同样核对身份)。
3. 双向验证:test 写私有区后,**default 以其自身 key 的常规数据面**不可见;
   test 也不可读取 default 既有私有资源。注意 default 是 ADMIN,按 §2.4 仍可
   通过列举/轮换 test key 间接取得 test 数据;本断言只验证普通数据面,不构成
   对 default/root 管理权限的隔离。**注意 default 的 user 私有区当前只有
   `.abstract`/`.overview` 骨架,没有可测资源;现有 8 个冒烟文件在共享区
   `viking://resources`,test 可以读取。执行前须先在
   `viking://user/default/resources/` 放一个专用私有夹具,再用 test 验证
   不可读;不要拿共享区冒烟资源当"default 私有资源"。**
   执行时先运行 `ov ls viking://resources` 与 `ov ls viking://agent` 记录
   基线(当前 resources 含既有 `deployment-smoke*` 与根元数据,agent 含系统
   骨架;若已按 README 清理,以当次输出为准),验收后比对两者,不得假定
   基线为空。**双向验证结束后必须删除 default 与 test 的专用私有夹具并核对
   删除结果**,不得只在共享区做清理而把测试残留留在 default 私有区。
4. README 增补三身份表、key 位置与测试纪律;引用本方案文档。同步改写
   README 既有行中与本文冲突的指引:"认证凭据位置"一节"创建 test 配置时
   不要用 `--name local`……应使用独立 name"的持久 test profile 用法
   (本文 §4/§5.1 已改为默认临时 600 配置 + 验收后删除),验收时以本文
   为准(2026-11-03 检视:批准前两文并存相反默认,按 README 行事会绕过
   本文的多副本治理)。
5. (独立后续,另行批准)root key 创建 `meteor`(role=user),双向验证其与 test
   均不可读取对方私有区,并清理双方专用夹具。宿主安装前先以不输出值的 JSON
   字段检查确认 `~/.openviking/ovcli.conf` **不含 `root_api_key`**;若旧配置含有,
   必须先经批准迁移到全新 `OPENVIKING_HOME` 或安全移除该字段,因为官方安装器
   会保留未指定字段,不能靠传入 meteor `api_key` 清除 root 能力。meteor key
   通过交互无回显输入或既有 600 配置提供,写入宿主 `~/.openviking/ovcli.conf`
   (600),不在 repo `state/secrets/` 另存副本;安装后再次确认 agent 配置只有
   meteor user key,没有 default/root key。
   统一安装器支持的 harness 用**一次逗号分隔 `--harness` 调用**批量安装
   (如 `claude,opencode,pi,dsh`,按需显式加入 `cli`),避免逐工具重复下载、
   服务探测、配置写入和时间戳备份;Hermes/OpenClaw/Agent Plugins 1.0 等不在
   列表中的工具仍走各自官方流程。记录实际 CLI/plugin 版本、artifact hash 与
   auto-update 状态,形成与 v0.5.2.1 的兼容基线;`@latest`/host 自动更新后必须
   重新验收,不得把 server digest 固定误当成客户端也固定,也不得未经批准修改
   用户全局 auto-update 配置。
   每个 harness 验收必须包含真实 MCP handshake/tools call 及代表性 read/find/
   write/delete 数据面闭环;自动召回/捕获在 test 身份或独立测试账户的专用
   peer/私有 URI 中验证并清理,不用 meteor 个人偏好做实验。安装器 "Server check
   passed"、插件出现在列表及 `ov health` 身份正确均只是诊断,不能替代这些能力
   验收。**若 §4 的 hashing 未启用/未确认,服务端 users.json 仍会保存 meteor
   key 的明文副本(§4),因此"只写入宿主"不成立;备份/迁移 `state/` 即包含它。**

任一 user 创建成功但 key 本地落盘失败时,不得重复创建 user;用 root key 调
`POST /api/v1/admin/accounts/default/users/<uid>/key` 轮换,再将返回的新 key
原子写入上述对应位置(600)并重跑身份与双向隔离验证。

## 7. 开放点(批准前可调整)

- user 命名(`meteor` / `test` 为当前拟定);
- §1.3 的绝对"完全分离"是否仍是硬性要求:若是,本同账户方案不满足,须另案
  设计独立账户/实例与独立 OS 凭据边界,不能以本方案验收替代;
- 部署设计 §9.4 与 README 的端到端验收命令是否改写到 test 私有区(依赖 §5.2 的
  `ov add-resource` 私有路径实测与 §6.3 的双向验证)。

## 8. 方案修正记录(避免回退)

- 2026-11-03 全项目检视修正(仅文档,不执行):§5.5 修正"容器 tmpfs"事实
  (openviking service 未配 tmpfs,`/tmp` 为可写层,restart 后残留);§6.2
  硬编码清单补全(CLI_PROFILE_NAME、DEFAULT_BASE_URL 静默代入、语言门禁
  位置)并明确 /tmp 位置下 saved-profile 检查的预期跳过语义;§5.1 明确
  `code_auth` 的抽取目标与禁 source 矩阵脚本;§2 增补未落盘实测的取证
  说明;§6.4 列出 README 冲突行清单。同批脚本侧修复
  (`_ov_health_ok` 移除无依据顶层信封回退、改三态返回并把 ov 超时判为
  RC_UPSTREAM、`_cli_config_matches` 拆纯谓词消除双重解析)见当次提交。
- 2026-11-02 检视修正(仅文档与实现预核,不执行):§6.2 补齐 `_ov_health_ok`
  helper 两处与 default 绑定的硬编码(saved profile 固定名 `ovcli.conf.local`、
  `root_api_key` 强制比较)的参数化要求;§5.5 增加临时 test 配置不得残留
  `state/`(优先容器 /tmp,防崩溃残留随备份扩散;2026-11-03 修正其存储
  性质表述)的机制要求;§6.1 明确
  参数化需当场修改 `bootstrap_auth.py` 且属批准范围内的实现工作。
- 2026-11-02 全项目复核:明确同账户/同 UID 方案不满足绝对"完全分离";test
  默认只保留 key 文件并使用安全 direct curl,健康检查用临时配置;补齐完整
  identity 命令、私有夹具清理、真实 MCP/数据面验收、批量 harness、客户端
  版本兼容基线,以及宿主旧 `root_api_key` 不得随安装器合并保留的前置门禁。
- 2026-11-02 评审补充:明确同 UID 协作式隔离边界;default/root key 权限不属
  §5.4 已接受残留;§6.3 的 default 可见性断言仅限普通数据面;共享面扩大到
  整个 `viking://agent` 命名空间并纳入残留基线;`ovcli.conf.local` 是 CLI
  saved profile 而非手工副本。
- 2026-11-02 官方接入复核关闭 `meteor` 是否需要 admin 的开放点:普通 USER
  key 已满足官方数据面、MCP、CLI 与安装器接入;当前部署 v0.5.2.1 的 key 轮换
  仍走 ROOT/ADMIN 管理路径,详见 §2.4。
- 早期方案曾提议"每工具一个 peer(claude-code/opencode/pi…)"与"每工具独立
  user key"(选项 A/B/C)——**已被官方证据否决**:官方 peer 轴是仓库维度而非
  工具维度;官方插件生态为单 user key 共享。未来讨论不得回到该版本。
- 早期"会话自定命名区分工具"亦多余:官方会话 ID 前缀已天然区分来源。
- 2026-11-04 用户批准并完成实施:test/meteor 身份与双向隔离、Claude Code 和
  OpenCode 2.x 官方接入、真实 MCP/数据闭环及双向召回均已验收;实际结果与
  managed/standalone 边界见 `docs/testing/2026-11-04-user-identity-and-agent-integrations.md`。
