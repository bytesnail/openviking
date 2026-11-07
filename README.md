# OpenViking v0.5.2.1 本机 Docker Compose 部署

本机单用户部署:OpenViking v0.5.2.1(镜像 tag+digest 固定,不拉 latest;2026-11-01 自
v0.5.2 升级并从零重建,过程与验收见 git 历史)复用
`/mnt/usr/tools/model-serving` 的 `embeddinggemma-2`(127.0.0.1:18351)与
`qwen3-reranker-0.6b`(127.0.0.1:18352,纯文本;2026-10-29 起接替多模态
`qwen3-vl-reranker-2b`;客户端仅发送文本,换型影响与验证见
`docs/testing/2026-11-01-reranker-model-adaptation.md`),VLM 走 Z.AI GLM Coding Plan
`glm-5.3-flash`,多模态 embedding 经 loopback 适配器(127.0.0.1:18353)。

本 README 是**索引与操作值注册表**;流程、判据与细则在 `docs/ops/` 活文档:

- 部署与日常运行(前提、bootstrap 退出码、适配器参数):[deploy.md](docs/ops/deploy.md)
- 验收与测试身份纪律(完整判据、e2e、端到端 §9.4):[acceptance.md](docs/ops/acceptance.md)
- 备份、恢复演练与 vectordb 索引恢复:[backup-recovery.md](docs/ops/backup-recovery.md)
- 升级与回滚(含 embedding 迁移约束):[upgrade.md](docs/ops/upgrade.md)
- Agent 接入(Claude Code / OpenCode):[agent-integration.md](docs/ops/agent-integration.md)
- 检索与 VLM 链路运行事实(2026-11-07 核查):[runtime-behavior.md](docs/ops/runtime-behavior.md)

操作值权威来源只有三处:本 README(注册表:端口拓扑权威表、三身份与凭据位置、
验收判据入口)、`compose.yaml`、`config/ov.conf`;改任何操作值前先读本 README
对应章节,并在同一变更中同步更新。`docs/ops/` 为活文档,随实施同步维护;其余
`docs/`(testing、research、superpowers/specs、superpowers/plans,按日期命名)是
历史决策与验收档案,示例值可能已被后续实施取代(历史设计档案:
`docs/superpowers/specs/2026-10-25-openviking-compose-design.md`)。

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
- 同账户 user 隔离只是服务端普通数据面的协作式隔离:同 UID 进程仍能读取本机
  凭据,ADMIN/ROOT 仍有管理面间接访问能力;共享区、模型 token、向量库与任务
  队列也不是 user 级隔离边界。需要强隔离时必须另用独立账户/实例与 OS 边界。
- 不启用 VikingBot(无 18790 Gateway);`ov.conf` 仅保存 `${VAR}` 引用,真实
  凭据位置见下节。

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

## 三身份与凭据位置

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
  (当前文件 644,同 UID 可读),并随 `state/` 进入备份;该文件与备份同样
  禁止读取回显、上传或入库。`state/` 的 700 只对同机其他 OS 用户生效,
  不构成对同 UID agent/测试进程的隔离边界。
- default CLI 配置:`state/ovcli.conf`(active)与 `state/ovcli.conf.local`
  (saved profile,由 `ov config add --name local` 写入)均须 600。bootstrap
  幂等核对二者的 URL、user/root key 与身份,并同步有限且 `>=300` 的
  timeout;不一致时保留现场并进入人工恢复,不得自动 `--force` 覆盖(细则
  见 [deploy.md](docs/ops/deploy.md))。
- test 默认**不创建持久 CLI active/saved profile**:身份检查统一
  `bootstrap_auth.py check-user`(生成仅含 test key 的临时 600 配置、断言
  `default/test/user`、用后即删,命令见
  [acceptance.md](docs/ops/acceptance.md));需要轮换时用
  `bootstrap_auth.py rotate-user --user-id test --key-file ...`,不要重复
  创建 user。专项持久 CLI 测试仍须单独批准,并使用独立 profile 名。
- `state/ov.conf`(root 属主、0 字节)是 dockerd 在 bind 挂载外层 state 内
  创建的挂载点存根,容器内被真实 `config/ov.conf` 覆盖,非凭据,勿删。
- Studio(`http://127.0.0.1:1933/studio`)登录用 User key:在本地终端自行
  读取,不要复制进对话或日志;agent 会话中的凭据纪律见 AGENTS.md「硬性纪律」。

## 部署与日常运行

```bash
cd /mnt/usr/tools/openviking
./scripts/prepare.sh
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose exec -T openviking python -I -B /opt/openviking/scripts/bootstrap_auth.py
```

`prepare.sh` 幂等但须在栈停止时运行(要求 1933/18353 空闲)。前提清单、
bootstrap 细则与退出码表、适配器运行参数见 [deploy.md](docs/ops/deploy.md)。

```bash
docker compose ps                 # 状态
docker compose restart openviking # 重启主服务(state 保留)
docker compose down               # 停止(保留宿主 state,不清数据)
docker compose up -d --wait --wait-timeout 180   # 再次启动
```

配置变更与例行验证的选择、日志查看纪律见 [deploy.md](docs/ops/deploy.md)。

## 验收

```bash
docker compose exec -T openviking openviking-server doctor
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py
docker compose exec -T openviking ov health -o json
curl -fsS http://127.0.0.1:1933/health
curl -fsS http://127.0.0.1:1933/ready
```

判据要点:`doctor` 全项通过(embedding 探针为真实请求,返回 768 维);
`smoke_models.py` 为 2 次 GLM 真实调用 + 1 次 rerank 真实请求,消耗 Coding
Plan 用量;`ov health -o json` 须含 `"healthy": true` 且 `default/default`、
`role=admin`(**`role=root` 或降级后的 `role=user` 均判失败**)。完整判据、
e2e 认证矩阵与端到端 §9.4 见 [acceptance.md](docs/ops/acceptance.md)。

## 备份与恢复

```bash
docker compose stop
TS=$(date +%Y%m%d-%H%M%S)
tar -czf "backups/openviking-${TS}.tar.gz" state config scripts tests compose.yaml secrets.env
chmod 600 "backups/openviking-${TS}.tar.gz"
docker compose up -d --wait --wait-timeout 180
```

备份含密钥:不打印内容、不上传、不入库。恢复演练**不得与生产栈并行**;
vectordb 索引损坏恢复不依赖未损坏旧快照——完整流程见
[backup-recovery.md](docs/ops/backup-recovery.md)。

## 升级与回滚

先备份 → 人工选定新 release(核对官方 tag、源码与镜像 digest,不拉
`latest`)→ 更新 `compose.yaml` 的 tag+digest、复核 `ov.conf` schema 与适配器
协议 → 启动后重跑全部验收。回滚须连配置与状态一起回退,不只回滚镜像;
embedding 模型/维度变化的迁移约束见 [upgrade.md](docs/ops/upgrade.md)。

## 目录

```text
compose.yaml            双 service 定义(host 网络 + loopback)
config/ov.conf          OpenViking 配置(密钥为 ${VAR} 引用)
scripts/prepare.sh      前置检查/密钥/目录/标记
scripts/embedding_adapter.py  多模态协议适配器(18353)
scripts/bootstrap_auth.py     api_key 初始化(账户/密钥/CLI)
scripts/env_file.py           secrets/config 只读解析与漂移校验(不回显值)
scripts/smoke_models.py       模型链路冒烟
tests/                  离线单元测试、e2e 验收脚本与 fixtures
state/                  OpenViking 工作区(敏感,700)
backups/                本地备份(敏感,700)
secrets.env             凭据(600,不入库)
docs/ops/               流程细则活文档(部署/验收/备份/升级/Agent/运行事实)
docs/                   历史决策/验收/研究档案(示例值可能已被取代)
AGENTS.md               agent 项目指令(细节指针化,操作值以本 README 为准)
```

## 条款提示

GLM Coding Plan 官方文档限定订阅用于其支持的工具列表(未列 OpenViking)。
本部署仅作本机个人使用;若需严格遵循条款,应改用 Z.AI Standard API 产品
并相应更换 endpoint/key(见设计文档 §12)。
