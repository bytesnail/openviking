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
- 认证为 `api_key` 模式:root key 管理账户,User key 访问数据;两者分离。
  **但 v0.5.2.1 的 default user 是 ADMIN,可用 `GET /api/v1/admin/accounts/default/users`
  (默认 `include_credentials=true`)取得本账户所有 user 的明文 key,并可轮换
  他人 key;因此 default/root key 在效果上可读取所有 user 数据,必须按最高
  敏感级保护。**
- 不启用 VikingBot(无 18790 Gateway)。
- 真实凭据的完整清单、位置与权限见下节"认证凭据位置";`ov.conf` 仅保存
  `${VAR}` 引用。

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

### 认证凭据位置(敏感,勿粘贴到任何对话/issue)

- root key:`secrets.env` 的 `OPENVIKING_ROOT_API_KEY`(容器环境注入),以及
  bootstrap 生成的 active 配置 `state/ovcli.conf` 与 CLI saved profile
  `state/ovcli.conf.local`。
- User key:`state/secrets/user_api_key`(600),**以及服务端存储
  `state/data/viking/default/_system/users.json`(当前 644;该文件保存每个
  user 的明文 key,随 `state/` 进入备份)**。
- CLI 配置:`state/ovcli.conf`(active)与 `state/ovcli.conf.local`(后者是
  `ov config add --name local` 写入的 **CLI saved profile**,不是手工副本;
  bootstrap 通过该命令间接写入)。saved profile 路径固定为同目录
  `ovcli.conf.<name>`,与 active 配置文件名无关;创建 test 配置时不要用
  `--name local`(会覆盖 default 的 saved profile),应使用独立 name。两者均
  600,URL 与 User/root key 必须一致,timeout 均须为有限且 `>=300` 的数值;
  轮换/清理任一密钥时须同步处理两份配置。bootstrap 会同时为 active
  配置与 saved profile 补 `"timeout": 300`(已有则不覆盖),并核对两者的 URL、
  User/root key 与目标部署一致;active 丢失或中断写坏时,只在 saved profile
  与目标 URL/key 完全一致时原子恢复 active,不自动 `--force` 覆盖 saved。
  不一致时进入人工恢复并保留现场。轮换 user key 后 users.json 由 Admin API
  更新,但旧备份仍含旧 key。
- `state/ov.conf`(root 属主、0 字节)是 dockerd 在 bind 挂载外层 state 内
  创建的挂载点存根,容器内被真实 `config/ov.conf` 覆盖,非凭据,勿删。

Studio(`http://127.0.0.1:1933/studio`)登录用 User key:在本地终端自行
`cat state/secrets/user_api_key` 读取,不要复制进对话或日志。**不要在 agent
会话或工具调用中执行该 `cat`**(会把明文带进 transcript);agent 需要凭据值时
只能走变量中转或 `read -s` 式无回显输入。
`state/data/viking/default/_system/users.json` 保存同一批明文 key(当前 644,
同 UID 可读),同样禁止 cat/Read/粘贴;`state` 的 700 只对同机其他 OS 用户
生效,不构成对同 UID agent/测试进程的隔离边界。

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
  `bash tests/e2e_auth_matrix.sh`(基础认证边界 9 项;
  `state/secrets/test_user_api_key` 存在时自动追加 4 项 USER 边界,否则
  SKIP;完整 USER 验收用 `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`,使缺失
  USER 行直接失败)不消耗 GLM,可随时重跑;矩阵只覆盖状态边界,不替代方案
  §6.3 的私有读写隔离检查。普通 USER 边界需先按方案 §6.1 创建 test 并重跑。
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

## 升级与回滚

1. 先备份(同上)。
2. 人工选定新 release,核对官方 tag、源码与镜像 digest;不拉 `latest`。
3. 更新 `compose.yaml` 的 tag+digest,复核 `ov.conf` schema 与适配器协议。
4. 启动后重跑全部验收。

回滚:停止并保留异常状态 → 恢复与旧镜像匹配的配置与状态备份 → 旧
tag+digest 启动 → 重跑验收;不只回滚镜像而保留可能不兼容的新状态。
Embedding 模型或维度变化必须走官方迁移/reindex 或新建空工作区。

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
