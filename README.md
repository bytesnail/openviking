# OpenViking v0.5.2.1 本机 Docker Compose 部署

本机单用户部署:OpenViking v0.5.2.1(镜像 tag+digest 固定;2026-11-01 自
v0.5.2 升级并从零重建,过程与验收见 git 历史)复用
`/mnt/usr/tools/model-serving` 的 `embeddinggemma-2`(127.0.0.1:18351)与
`qwen3-reranker-0.6b`(127.0.0.1:18352,纯文本;2026-10-29 起接替多模态
`qwen3-vl-reranker-2b`;客户端仅发送文本,换型影响与验证见
`docs/testing/2026-11-01-reranker-model-adaptation.md`),VLM 走 Z.AI GLM Coding Plan
`glm-5.3-flash`,多模态 embedding 经 loopback 适配器(127.0.0.1:18353)。

历史设计档案(决策依据、原始参数理由与失败处理；其中镜像、reranker 等示例已被后续实施取代，当前操作值只以本 README、`compose.yaml` 与 `config/ov.conf` 为准):`docs/superpowers/specs/2026-10-25-openviking-compose-design.md`。

## 边界

- 一切服务只监听 `127.0.0.1`(API 1933、适配器 18353),无 `ports` 映射、无反向代理。
- 适配器 healthcheck 探 `/health`,liveness 只量测适配器进程自身;`/ready`
  含上游(model-serving)探活,仅用于验收命令与人工诊断——上游停滞时重启
  适配器无意义,不得把上游故障转化为适配器重启循环(2026-11-03 检视决策)。
- 认证为 `api_key` 模式:root key 管理账户,User key 访问数据;两者分离。
  **但 v0.5.2.1 的 default user 是 ADMIN,可用 `GET /api/v1/admin/accounts/default/users`
  (默认 `include_credentials=true`)取得本账户所有 user 的明文 key,并可轮换
  他人 key;因此 default/root key 在效果上可读取所有 user 数据,必须按最高
  敏感级保护。**
- 不启用 VikingBot(无 18790 Gateway)。
- 真实凭据的完整清单、位置与权限见下节"认证凭据位置";`ov.conf` 仅保存
  `${VAR}` 引用。

### 端口拓扑与同步点(权威表)

model-serving 属 `/mnt/usr/tools/model-serving` 仓库,本仓库是其消费者。任一
端口变更须同步表中"本仓库落点"全部位置;漏改后果不一:compose/prepare 为
硬失败,`scripts/env_file.py` 漂移检查与 e2e 兜底为静默降级,`config/ov.conf`
为运行时报错。

| 端口 | 服务 | 谁消费 | 本仓库落点 |
|---|---|---|---|
| 1933 | OpenViking API(`openviking` 服务) | CLI/插件/bootstrap/全部验收 | `compose.yaml OPENVIKING_SERVER_PORT`、`scripts/prepare.sh` 空闲门禁、`scripts/bootstrap_auth.py DEFAULT_BASE_URL`、`tests/e2e_auth_matrix.sh` 与 `tests/e2e_user_isolation.sh` 的 `BASE` 兜底默认 |
| 18351 | model-serving embedding(`embeddinggemma-2`) | 适配器上游;漂移检查;e2e 探针 | `compose.yaml EMBEDDING_UPSTREAM_URL`、`scripts/embedding_adapter.py DEFAULT_UPSTREAM`、`scripts/env_file.py check_model_drift`、`scripts/prepare.sh` loopback 门禁、`tests/e2e_adapter_probe.py` 兜底默认 |
| 18352 | model-serving rerank(`qwen3-reranker-0.6b`,纯文本) | OpenViking 服务器直连(不经适配器);漂移检查 | `config/ov.conf` rerank `api_base`、`scripts/env_file.py`、`scripts/prepare.sh` |
| 18353 | 多模态 embedding 适配器(`embedding-adapter` 服务) | OpenViking 服务器、healthcheck、e2e 探针 | `compose.yaml EMBEDDING_ADAPTER_PORT`、`compose.yaml` healthcheck(`http://127.0.0.1:18353/health`)、`scripts/embedding_adapter.py DEFAULT_PORT`、`scripts/prepare.sh` 空闲门禁、`tests/e2e_adapter_probe.py` 兜底默认 |

两层层级为有意设计:ov.conf 的 embedding `api_base` 指向 18353(适配器)而
非 18351;rerank 纯文本直连 18352。

### 适配器环境变量(默认值见 scripts/embedding_adapter.py)

- `EMBEDDING_ADAPTER_HOST`(默认 127.0.0.1)/`EMBEDDING_ADAPTER_PORT`
  (默认 18353):host 仅接受 IPv4 回环,`::1` 等以明确配置错误退出。
- `EMBEDDING_UPSTREAM_URL`(默认 `http://127.0.0.1:18351/v1/embeddings`):
  启动时回环校验,防止请求体(用户文本+base64 图像)外发到非回环主机。
- `EMBEDDING_UPSTREAM_TIMEOUT_SECONDS`(默认 120):上游转发超时;与单请求
  总 deadline、空闲读超时共同限定单连接最长占用。
- `EMBEDDING_MAX_BODY_BYTES` / `EMBEDDING_MAX_RESPONSE_BODY_BYTES`
  (默认各 64 MiB):入站请求与上游 200 响应的大小上限。
- `EMBEDDING_MAX_CONCURRENT`(默认 2,compose 显式设 2):并发上游转发上限。
- `EMBEDDING_MAX_CONNECTIONS`(默认 16,compose 未覆盖):并发连接硬上限。
  额度在 accept 主循环获取,慢 header 连接同样计入;满额时新连接完成握手后
  滞留内核 backlog,等任一额度释放才被接受——持续饱和期间 `/health` 同样
  排不上队,healthcheck 会将适配器标为 unhealthy(诚实信号;本部署不按
  unhealthy 自动重启,2026-11-06 决策,见 compose.yaml 注释)。行为由
  `tests/test_embedding_adapter.py::test_connection_handler_threads_are_bounded`
  钉死,是有意的背压设计而非缺陷。

## 前提

- 宿主需有 `bash`、`python3`(`prepare.sh` 的 secrets/config 校验与密钥生成)、
  `curl`(认证矩阵)、`ss`(端口检查)、`tar`(备份)与 Docker Compose。
- `secrets.env` 已存在且含非占位 `Z_AI_API_KEY`(缺失时 `prepare.sh` 会明确报错);`OPENVIKING_ROOT_API_KEY` 缺失时由 `prepare.sh` 自动生成 32 字节 hex,手工提供时须非占位且至少 16 字符。
- `model-serving/bin/serve.sh status` 显示两个模型运行(18351/18352 在位)。
- 当前用户 UID/GID 为 `1000:1000`(与 `compose.yaml` 的 `user` 一致;其他用户部署须先显式改 Compose 与 `scripts/prepare.sh` 中的常量)。
- 端口 1933、18353 空闲。
- Docker Engine ≥ 支持 compose `--wait`、镜像
  `ghcr.io/volcengine/openviking:v0.5.2.1@sha256:7c26f9777c7632fd4b771be77471634bffb6ec8d6338b39f53deb2da1b1d34b3`(index digest,amd64;镜像 label revision 与 tag commit `f5e6203033a1` 互证)。

## 部署(首次)

```bash
cd /mnt/usr/tools/openviking
./scripts/prepare.sh
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose exec -T openviking python -I -B /opt/openviking/scripts/bootstrap_auth.py
```

`prepare.sh` 幂等,可重复执行,但**须在 compose 栈停止时运行**:端口检查
要求 1933/18353 空闲,栈在运行时会按设计失败(占用者就是本部署)。
`secrets.env` 中 `OPENVIKING_ROOT_API_KEY` 缺失时自动生成(32 字节 hex),
存在但为空时直接失败。

`bootstrap_auth.py` 面向容器内执行(默认 `OPENVIKING_CLI_CONFIG_FILE=
/app/.openviking/ovcli.conf`、user key `/app/.openviking/secrets/user_api_key`
均为容器内挂载路径)。宿主直跑只在显式覆盖 `OPENVIKING_CLI_CONFIG_FILE`
与 `OPENVIKING_USER_KEY_FILE` 后才有意义;另可用 `OPENVIKING_BOOTSTRAP_URL`
与 `OV_BIN` 覆盖服务地址与 CLI 可执行名(2026-11-03 检视补记)。

退出码(全部子命令统一;200+非 ok 信封在首次初始化与 provision/rotate 间
同映射 RC 7,2026-11-07 统一):

| RC | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | 初始化完成或幂等确认已初始化 |
| 2 | 参数/环境不合法 | provision 未指定恰一个输出;env 覆盖冲突 |
| 4 | 需人工恢复(现场已保留) | 账户/user 已存在而本地 key 缺失;key 落盘失败;saved profile 与目标不一致 |
| 5 | 服务端故障 | admin API 非 200/不可达/超时;凭据未受质疑,恢复后重跑 |
| 6 | root key 被拒 | 401/403(无效或已轮换) |
| 7 | 响应缺 user key/信封非 ok | 服务端回归或协议漂移;所有子命令同一映射 |
| 8 | CLI 配置写回失败 | timeout=300 无法确保;`ov config add` 失败 |
| 9 | 验收失败 | check-user 的 health/身份核对不达 |

### 三身份与认证凭据位置(敏感,勿粘贴到任何对话/issue)

| 身份 | user_id / role | 用途 | 持久凭据位置 |
|---|---|---|---|
| 部署运维 | `default` / `admin` | bootstrap、doctor/smoke、部署验收 | `state/secrets/user_api_key` 与 default CLI 配置 |
| 正式个人 | `meteor` / `user` | Claude Code、OpenCode 的真实记忆与资源 | 宿主 `~/.openviking/ovcli.conf`(600),repo 不另存 key |
| 测试 | `test` / `user` | 数据面功能测试、隔离与插件实验 | `state/secrets/test_user_api_key`(600) |

root/default key 均属 crown-jewel:在 v0.5.2.1 可经 Admin API 列举或轮换
user key,不得放进 Agent 或测试夹具。三身份方案及隔离边界详见
[多身份与多 Agent 方案](docs/superpowers/specs/2026-11-01-user-identity-and-multi-agent-plan.md)。

- root key:`secrets.env` 的 `OPENVIKING_ROOT_API_KEY`(容器环境注入),以及
  bootstrap 生成的 active 配置 `state/ovcli.conf` 与 CLI saved profile
  `state/ovcli.conf.local`。
- User key:default 在 `state/secrets/user_api_key`,test 在
  `state/secrets/test_user_api_key`,均为 600;meteor 只写宿主
  `~/.openviking/ovcli.conf`(600),repo 不另存裸 key。服务端
  `state/data/viking/default/_system/users.json` 另存每个 user 的明文 key
  (当前文件 644),并随 `state/` 进入备份;该文件与备份同样禁止读取回显、
  上传或入库。
- default CLI 配置:`state/ovcli.conf`(active)与 `state/ovcli.conf.local`
  (saved profile,由 `ov config add --name local` 写入)均须 600。bootstrap
  幂等核对二者的 URL、user/root key 与身份,并同步有限且 `>=300` 的 timeout;
  不一致时保留现场并进入人工恢复,不得自动 `--force` 覆盖。旧 default 配置
  缺省 `url` 时兼容文档化 loopback 默认值;新临时身份配置必须显式写 URL。
- test 默认**不创建持久 CLI active/saved profile**。身份检查统一用
  `bootstrap_auth.py check-user`,它生成仅含 test key 的临时 600 配置、显式
  设置 CLI 配置路径、断言 `default/test/user`,并在结束后删除。需要轮换时
  使用 `bootstrap_auth.py rotate-user --user-id test --key-file ...`,不要重复
  创建 user。专项持久 CLI 测试仍须单独批准,并使用独立 profile 名。
- 同账户 user 隔离只是服务端普通数据面的协作式隔离:同 UID 进程仍能读取本机
  凭据,ADMIN/ROOT 仍有管理面间接访问能力;共享区、模型 token、向量库与任务
  队列也不是 user 级隔离边界。需要强隔离时必须另用独立账户/实例与 OS 边界。
- `state/ov.conf`(root 属主、0 字节)是 dockerd 在 bind 挂载外层 state 内
  创建的挂载点存根,容器内被真实 `config/ov.conf` 覆盖,非凭据,勿删。

Studio(`http://127.0.0.1:1933/studio`)登录用 User key:在本地终端自行
`cat state/secrets/user_api_key` 读取,不要复制进对话或日志。**不要在 agent
会话或工具调用中执行该 `cat`**(会把明文带进 transcript);agent 需要凭据值时
只能走变量中转或 `read -s` 式无回显输入。
`state/data/viking/default/_system/users.json` 保存同一批明文 key(当前 644,
同 UID 可读),同样禁止 cat/Read/粘贴;`state` 的 700 只对同机其他 OS 用户
生效,不构成对同 UID agent/测试进程的隔离边界。

### 测试身份纪律

凡会写数据或验证用户数据面的功能测试一律使用 test 身份,目标优先
`viking://user/test/resources/...`;不写 `viking://agent/*`,不写真实记忆。
项目脚本 source 不含密钥加载逻辑的 `tests/curl_auth.sh`,以 direct curl、
stdin `-K -` 传 key,不得 source 整个认证矩阵。共享区写入仅限专门的共享语义
测试,并须使用专用 URI 和验收后清理。

```bash
# test 身份健康检查:临时配置位于容器 /tmp,命令完成后即删除
docker compose exec -T openviking python -I -B \
  /opt/openviking/scripts/bootstrap_auth.py check-user \
  --user-id test --role user \
  --key-file /app/.openviking/secrets/test_user_api_key \
  --config-file /tmp/ovcli.test.conf

# 强制 USER 权限边界 + default/test 双向私有隔离与夹具清理
REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh
USER_A=default USER_B=test \
  USER_A_KEY_FILE=state/secrets/user_api_key \
  USER_B_KEY_FILE=state/secrets/test_user_api_key \
  bash tests/e2e_user_isolation.sh
```

容器 `/tmp` 按 `compose.yaml` 为 tmpfs(`rw,noexec,nosuid`:openviking 256m、
embedding-adapter 16m;当前运行中的容器早于该加固,下次 `docker compose up
-d` 重建容器后生效):tmpfs 数据随容器停止清空、计入容器内存且有容量上限,
check-user 的临时身份配置因此不跨重启残留,但也不得把 `/tmp` 当持久存储。
上述隔离验收只证明普通数据面私有读写,不构成对 ADMIN/ROOT 或同 UID
进程的安全边界。

## Agent 接入(仅 Claude Code 与 OpenCode 2.x)

正式 Agent 共用宿主 `~/.openviking/ovcli.conf` 中的 meteor USER key,沿用官方按
仓库派生 peer 的机制;不逐工具创建 user/key/peer,也不把 default/root key 放进
Agent。当前基线为 Claude Code 2.1.316 + `openviking-memory@openviking` 0.7.21、
OpenCode v2.2.5 + `@openviking/opencode-plugin` **2026.11.5-1**。版本升级后须重新验收。

### OpenCode:package 插件与自动 MCP 注册

当前 OpenCode 使用 package 插件。将包名合并进现有 `plugins` 数组,不要覆盖其他插件:

```jsonc
{
  "plugins": [
    "@openviking/opencode-plugin"
    // 保留宿主已有的其他 package 插件
  ]
}
```

OpenCode v2 也兼容官方 `plugin` 写法的规范化,但本机配置已使用原生 `plugins`,不要
同时维护两套键。**不要手工保留 `mcp.openviking` 的本地绝对路径**:package 插件会从
自己的 `pluginRoot` 自动注册 package 内的 `servers/mcp-proxy.mjs`,URL、身份和 key 仍由
`ovcli.conf` 或 `OPENVIKING_*` 提供。

2026-11-04 的一键安装器曾同时写入 `plugins/openviking/` 与 `plugins/openviking.js`;
在 OpenCode 2.2.5 中二者被重复扫描,目录实例 active、wrapper 实例以
`Duplicate plugin ID: openviking` 失败。用户已于 2026-11-06 将这两个入口移至
`~/.config/opencode/plugins_bak/` 并迁移到 package。重跑旧安装流程前须先确认其生成物,
不得重新引入多个同名入口。

验收不能只看 MCP connected 或安装器摘要:还须核对 `/api/plugin` 中只有一个预期来源
且 active 的实例、`/api/mcp` 为 connected,并确认实际 proxy 路径指向当前 package。
Claude Code 则使用 URL marketplace 的 archive 插件 `openviking-memory@openviking`,当前
marketplace `autoUpdate=true`;其实际升级同样不能免验收。

### test 身份下的真实宿主复验

自动捕获/召回实验默认在沙箱 HOME 中使用 test 身份,不使用 meteor 的真实个人数据。
经用户明确许可在真实宿主目录验收时,也必须通过进程环境覆盖为 test key 和独立 peer;
OpenCode 使用 `--standalone`,因为 managed 服务不会继承 CLI 的身份覆盖环境,同时显式
关闭 stdin 并选择已配置的模型,避免等待输入或默认免费模型限额。可使用纯 env 连接,
避免另写临时 key 配置:

```bash
export OPENVIKING_CREDENTIAL_SOURCE=env
export OPENVIKING_URL=http://127.0.0.1:1933
export OPENVIKING_ACCOUNT=default
export OPENVIKING_USER=test
export OPENVIKING_PEER_ID=agent-e2e-package-<random>
export OPENVIKING_API_KEY="$(< state/secrets/test_user_api_key)"
export OPENVIKING_TIMEOUT_MS=300000
# 在获批准的测试目录中,选择已配置模型并关闭 stdin
opencode --standalone run --model zhipuai-coding-plan/glm-5.3-flash '...' </dev/null
```

MCP 参数以当次 schema 为准:当前 read 使用 `uris` 数组,write schema 未声明
`processing_mode`,不能假定它与 direct API 参数一致。清理除专用资源外,还须检查 peer 记忆、session 目录及自动生成的
`.abstract/.overview`,最后重新 find 测试 marker。完整迁移、schema、commit、跨 peer
自动召回与清理结果见
[OpenCode package 插件验收](docs/testing/2026-11-06-opencode-package-plugin-verification.md);
2026-11-04 的[身份与 Agent 接入验收](docs/testing/2026-11-04-user-identity-and-agent-integrations.md)
保留为历史基线。

## 验收

```bash
docker compose exec -T openviking openviking-server doctor
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py
docker compose exec -T openviking ov health -o json
curl -fsS http://127.0.0.1:1933/health
curl -fsS http://127.0.0.1:1933/ready
```

- `doctor` 须全项通过(embedding 探针为真实请求,返回 768 维)。
- `smoke_models.py` 发起 2 次 GLM 真实调用与 1 次 rerank 真实请求,消耗少量
  Coding Plan 用量;`ov health -o json` 须含 `"healthy": true`,且
  `result.account_id/user_id` 均为 `default`、`result.role` 必须为 `admin`
  (**`role=root` 或降级后的 `role=user` 均判失败**:root-only 配置也会返回
  default/default/healthy,而 USER default 无法执行所需运维管理)。
- 扩展功能面(2026-10-26 两轮全量通过):`python3 tests/e2e_adapter_probe.py`
  (适配器实况 8 项,含配置模型名与 /v1/models 加载列表比对)与
  `bash tests/e2e_auth_matrix.sh`(基础认证边界 9 项 + 4 项 USER 边界);
  本部署已创建 test,完整验收统一用 `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`,
  使缺失 USER 行直接失败。矩阵只覆盖状态边界,不替代上述 default/test
  私有读写隔离检查;USER 不可访问账户管理接口的断言对应 v0.5.2.1。
  矩阵的 `BASE` 仅允许 loopback HTTP URL,防止把 ROOT/ADMIN key 发往远端;
  curl 以 `-q` 禁用用户 curlrc。完整矩阵、发现(F1–F4)与复测方法见
  `docs/testing/2026-10-26-openviking-functional-test.md`。
- 端到端(§9.4):

```bash
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/deployment-smoke --wait
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/red.png --to viking://resources/deployment-smoke-image --wait
docker compose exec -T openviking ov find "紫鸢尾-7 号信标的校准码"
docker compose exec -T openviking ov ls viking://resources
```

- `--to` 指向已存在 URI 时是"替换"语义,旧内容会被更新掉:上述固定冒烟
  URI 在当前基线中已存在(见方案 §6.3),仅用于冒烟资源,勿指向真实数据;
  真实资源必须使用不存在的新 URI(多资源导入每资源独立 `--to`,或先
  `mkdir` 再用 `--parent`);若需保留旧冒烟内容,改用唯一 URI 或先删除。
- 大图(>4096px 任一边或 >10MB)摄入走切片管线:落库的是低清预览 +
  网格图 + tiles,**原图按设计不保存、不可 `ov get` 取回**;此类任务
  约 3 分钟,建议异步提交后轮询 `ov task list`(详见测试报告 §8.7)。
- 长文本语义成本 ≈ 0.195 万(约 1950)LLM tokens/千字符(50K 实测 9.7 万 tokens/10
  分钟;线性),1M 即约 195 万 tokens/3.3 小时;批量或超大文本用
  `--processing-mode vectors_only`(实测 20M 字符 21 分钟、零 GLM、深层
  检索正常),仅重点文档用默认语义模式(详见测试报告 §8.8)。
- 图片资源语义任务实测 64–110s,曾超过 CLI 60s 默认超时导致 `--wait` 报
  DEADLINE_EXCEEDED(服务端任务仍会完成)。CLI 顶层 `"timeout": 300`
  (无旗标,`ov config edit` 不含该项;`ov config show` 确认实际生效)
  现由 `bootstrap_auth.py` 生成与修复 `ovcli.conf` 时自动写入(已设置有限且
  `>=300` 的值不覆盖);active 与 saved profile 均由 bootstrap 同步处理,无需手工
  补写副本。若未来任务更慢仍超时,可继续调高该值,或退回不带 `--wait`
  提交 + 轮询 `ov task list`。
  同目录变更任务在途时,紧随的变更可能报 `[CONFLICT] lock acquire timed out`,
  排空后重试即可。

冒烟资源保留供检查;确认 URI 后可单独删除,不做自动递归清理
(2026-10-26 功能测试后已按此删除并验证,可用上述命令重建)。

## 日常操作

```bash
docker compose ps                 # 状态
docker compose restart openviking # 重启主服务(state 保留)
docker compose down               # 停止(保留宿主 state,不清数据)
docker compose up -d --wait --wait-timeout 180   # 再次启动
```

- 配置变更:编辑宿主 `config/ov.conf` → `docker compose restart openviking`;
  不支持热加载。例行验证按变更面选择:storage/auth/server 等非模型项先跑
  doctor/health 与相关认证检查;仅修改某模型段时跑对应探针;升级、回滚、
  跨组件变更与完整验收仍须重跑 doctor + 全量 smoke,避免无关编辑反复消耗 GLM。
- 查看日志:`docker compose logs --tail 50 openviking`。排查前先确认输出不
  含密钥;自定义脚本(适配器/bootstrap/smoke)已按"零密钥输出"编写。
- 不运行容器内 `env`/`printenv`,不输出完整 `docker inspect`。

## 检索与 VLM 链路事实(2026-11-07 核查)

逐项证据与实测数据见
[归档仓结论核查与 embedding 截断标定](docs/research/2026-11-07-archive-conclusion-verification.md);
此处只留运维必需结论。

- **query expansion 成功时日志静默**:状态只在检索响应体 `query_expansion`
  字段;失败才落 `Query expansion failed` WARNING。验收判据用"带
  `session_id` 的 context 检索无该 WARNING"——**不存在"日志变 used"这个
  信号**。expansion 仅在请求带 `session_id` 且会话有内容时尝试,不带
  session 的检索静默跳过。
- **query_planner 段整段替换 vlm**:该段一旦有配置,未配字段不继承 vlm
  而走自身默认(timeout 600s、max_concurrent 32);`max_tokens` 未配则
  不发该参数(由 z.ai 端默认决定)。当前 ov.conf 显式只配
  provider/model/api_base/api_key/extra_request_body。
- **`${VAR}` 展开静默残留**:`expandvars` 对未设置变量保留字面 `${VAR}`
  且容器照常 healthy;变量名拼错表现为对应后端静默 401,只能靠
  doctor/真实调用发现,启动日志无信号。
- **ov.conf 无热加载,`docker compose restart openviking` 即生效**:单文件
  bind 在容器重启时重新解析 inode(2026-11-07 实测:重启后容器内挂载
  md5 与宿主一致、新配置已加载)。旧归档仓"restart 无效须 down/up"
  说法不实,勿再引用;真实坑仅是"编辑后不重启"。
- **z.ai/glm-5.3-flash**:thinking 与 content 共享输出预算(日志实证
  `finish_reason=length` 且 content 空);coding 端点实测接受
  `thinking.type=disabled`(与 z.ai 文档页矛盾,以实测为准,thinking 是否
  真被关闭未能从外侧证实);vlm 段无法真正关思考,靠 `vlm.max_tokens`
  容纳。`vlm.max_tokens=65536`(2026-11-07 22:25 起)的验收判据:会话
  结束的 Phase 2 记忆提取不再出现 `output cap=` 截断 ERROR。
- **embedding 截断标定**(2026-11-07,生产路径实测 `usage.prompt_tokens`):
  估算器按 CJK 1 字=1 token、ASCII 4 字符=1 token;本栈真实卡实际/估算
  1.02–1.17(5 张大卡全实测),纯中文 0.77、英文散文 0.59、真实代码/脚本
  (Python/bash)1.03–1.22、紧凑配置(YAML)~1.39、符号密集合成样式 1.83、
  minified JSON ~2.08、base64 ≳2.75(后两类在现行 4096 下即失败,非提
  预算所引入;上游 #5895 的低估因子来自其他 tokenizer,不适用本栈)。
  记忆卡语料核查:126 张中仅 15 张含 fenced 块,最大单块 est 2234——
  "记忆卡整段引用代码"在真实语料中不成立。4096 现状下 meteor 名下 5 张
  大卡各 22–29% 内容向量不可见,全库估算最大 5790;**6144 可覆盖现存全部
  卡(生产函数直证,整卡实际 5925–6570 均在 slot 8448 内)且对真实代码
  类安全;仅紧凑配置类 est>~6078 顶部刀锋越界,失败为适配器可见 502**。
  是否调整待批。
- 上游未修锚点(2026-11-07):#3101(关机 flush 失败,索引中断源)、
  #5895/#5760/#6418(截断估算类)、sparse 本地 dense-only(#6109 收敛
  方向未合并、#7162 hybrid 损坏未修)。

## 备份与恢复

```bash
docker compose stop
TS=$(date +%Y%m%d-%H%M%S)
tar -czf "backups/openviking-${TS}.tar.gz" state config scripts tests compose.yaml secrets.env
chmod 600 "backups/openviking-${TS}.tar.gz"
docker compose up -d --wait --wait-timeout 180
```

备份含密钥:不打印内容、不上传、不入库;包含 `tests/` 是为了恢复后能使用
同一套 fixtures、smoke 与 E2E 验收。恢复演练**不得与生产栈并行**:先执行
`docker compose stop` 并保持生产停止,确认 1933/18353 未被其他进程占用;
再在新空临时目录解包,使用独立 Compose project name,沿用备份中的 loopback
端口与 CLI URL 完成验收。这样 restored `ovcli.conf`、health/curl 与固定 URI
写入都不可能打到在用生产。验收后先 `docker compose down` 停止恢复栈,确认
临时端口已释放,再回原目录启动生产;全过程不得覆盖原 `state/`。若确需并行
恢复,必须另行显式改写 Compose、ov.conf、active/saved CLI URL 及全部探针端点,
不能只改两个监听端口。

### vectordb 索引损坏恢复(v0.5.2.1 源码核实)

症状:容器 crash loop(引擎层 meta 加载失败异常直接传播),常见诱因是
异常中断(断电/OOM/`docker kill`)留下 0 字节
`state/data/vectordb/context/index/default/versions/<纳秒ts>/manager_meta.json`;
上游 #3101(SIGTERM 时 flush/close 失败)仍是现存中断源。恢复要点
(与 0.4.x 时代的关键差异:**不依赖未损坏旧快照**):

1. 停栈并先备份:`cp -a state/data/vectordb/context <临时备份目录>`(或走
   上节全量备份);任何 vectordb 状态改动前必做。
2. 定位损坏版本:
   `find state/data/vectordb/context/index/default/versions -name manager_meta.json -size 0`。
3. 删除损坏版本目录及对应 `<ts>.write_done` 标记;**必须保留
   `index/default/index_meta.json`**——它缺失时启动会跳过 recovery。
4. 重启:启动路径 `_recover()` 从 LevelDB store(`context/store/`)全量
   重建并重放 delta(日志特征 `Index 'default': replaying delta records
   lazily` → `replay complete`),数据本体(store/)不受索引损坏影响。
5. 验收:doctor + 冒烟检索命中;必要时 `content reindex`(`mode=
   vectors_only`,0.5.2.1 下真重建向量)兜底重算。

注意:正常 `close()` 只保留最新一个版本快照,**多版本快照不是安全网,
`backups/` 才是**;"删整个 index/ 指望重建"与"reindex 不生成向量"均为
0.4.x 旧说,0.5.2.1 已不成立(详证见
[归档仓结论核查](docs/research/2026-11-07-archive-conclusion-verification.md) §4)。

## 升级与回滚

1. 先备份(同上)。
2. 人工选定新 release,核对官方 tag、源码与镜像 digest;不拉 `latest`。
3. 更新 `compose.yaml` 的 tag+digest,复核 `ov.conf` schema 与适配器协议。
4. 启动后重跑全部验收。

回滚:停止并保留异常状态 → 恢复与旧镜像匹配的配置与状态备份 → 旧
tag+digest 启动 → 重跑验收;不只回滚镜像而保留可能不兼容的新状态。
Embedding 模型或维度变化必须走官方迁移/reindex 或新建空工作区;同维度
换模型/端点可配 `embedding.allow_metadata_override` 保留已索引数据
(改写 collection 元数据并告警,v0.5.2.1 schema),仅维度变化必须重建
(`collection_meta.json` 存 Dim 并严格校验)。

## 目录

```text
compose.yaml            双 service 定义(host 网络 + loopback)
config/ov.conf          OpenViking 配置(密钥为 ${VAR} 引用)
scripts/prepare.sh      前置检查/密钥/目录/标记
scripts/embedding_adapter.py  多模态协议适配器(18353)
scripts/bootstrap_auth.py     api_key 初始化(账户/密钥/CLI)
scripts/smoke_models.py       模型链路冒烟
tests/                  单元测试(适配器、认证初始化)+ fixtures
state/                  OpenViking 工作区(敏感,700)
backups/                本地备份(敏感,700)
secrets.env             凭据(600,不入库)
```

## 条款提示

GLM Coding Plan 官方文档限定订阅用于其支持的工具列表(未列 OpenViking)。
本部署仅作本机个人使用;若需严格遵循条款,应改用 Z.AI Standard API 产品
并相应更换 endpoint/key(见设计文档 §12)。
