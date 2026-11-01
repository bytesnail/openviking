# OpenViking v0.5.2.1 本机 Docker Compose 部署

本机单用户部署:OpenViking v0.5.2.1(镜像 tag+digest 固定;2026-11-01 自
v0.5.2 升级并从零重建,过程与验收见 git 历史)复用
`/mnt/usr/tools/model-serving` 的 `embeddinggemma-2`(127.0.0.1:18351)与
`qwen3-reranker-0.6b`(127.0.0.1:18352,纯文本;2026-10-29 起接替多模态
`qwen3-vl-reranker-2b`;客户端仅发送文本,换型影响与验证见
`docs/testing/2026-11-01-reranker-model-adaptation.md`),VLM 走 Z.AI GLM Coding Plan
`glm-5.3-flash`,多模态 embedding 经 loopback 适配器(127.0.0.1:18353)。

设计文档(决策依据、参数理由、失败处理):`docs/superpowers/specs/2026-10-25-openviking-compose-design.md`。

## 边界

- 一切服务只监听 `127.0.0.1`(API 1933、适配器 18353),无 `ports` 映射、无反向代理。
- 认证为 `api_key` 模式:root key 管理账户,User key 访问数据;两者分离。
- 不启用 VikingBot(无 18790 Gateway)。
- 真实凭据只在 `secrets.env`(600)、`state/secrets/user_api_key`、
  `state/ovcli.conf` 与 `state/ovcli.conf.local`(详见下节"认证凭据位置")
  与容器环境;`ov.conf` 仅保存 `${VAR}` 引用。

## 前提

- `secrets.env` 已存在且含非占位 `Z_AI_API_KEY`(缺失时 `prepare.sh` 会明确报错;`OPENVIKING_ROOT_API_KEY` 可由其自动生成)。
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

### 认证凭据位置(敏感,勿粘贴到任何对话/issue)

- root key:`secrets.env` 的 `OPENVIKING_ROOT_API_KEY`(容器环境注入),以及
  bootstrap 生成的 `state/ovcli.conf` 与其手工副本 `state/ovcli.conf.local`。
- User key:`state/secrets/user_api_key`(600)。
- CLI 配置:`state/ovcli.conf` 与 `state/ovcli.conf.local`(两者均 600、内容
  应一致,均含 User key 与 root key);前者由 bootstrap 生成,后者人工维护,
  bootstrap **不写**此文件,轮换/清理任一密钥时须同步处理两份配置。
- `state/ov.conf`(root 属主、0 字节)是 dockerd 在 bind 挂载外层 state 内
  创建的挂载点存根,容器内被真实 `config/ov.conf` 覆盖,非凭据,勿删。

Studio(`http://127.0.0.1:1933/studio`)登录用 User key:在本地终端自行
`cat state/secrets/user_api_key` 读取,不要复制进对话或日志。

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
  `result.account_id/user_id` 均为 `default`(排除无 key 或错身份的假通过)。
- 扩展功能面(2026-10-26 两轮全量通过):`python3 tests/e2e_adapter_probe.py`
  (适配器实况 7 项)与 `bash tests/e2e_auth_matrix.sh`(认证边界 8 项)不
  消耗 GLM,可随时重跑;完整矩阵、发现(F1–F4)与复测方法见
  `docs/testing/2026-10-26-openviking-functional-test.md`。
- 端到端(§9.4):

```bash
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/deployment-smoke --wait
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/red.png --to viking://resources/deployment-smoke-image --wait
docker compose exec -T openviking ov find "紫鸢尾-7 号信标的校准码"
docker compose exec -T openviking ov ls viking://resources
```

- `--to` 必须指向不存在的 URI:已存在时是"替换"语义,旧内容会被更新掉
  (多资源导入每资源独立 `--to`,或先 `mkdir` 再用 `--parent`)。
- 大图(>4096px 任一边或 >10MB)摄入走切片管线:落库的是低清预览 +
  网格图 + tiles,**原图按设计不保存、不可 `ov get` 取回**;此类任务
  约 3 分钟,建议异步提交后轮询 `ov task list`(详见测试报告 §8.7)。
- 长文本语义成本 ≈ 2 万 LLM tokens/千字符(50K 实测 9.7 万 tokens/10
  分钟;线性),1M 即约 195 万 tokens/3.3 小时;批量或超大文本用
  `--processing-mode vectors_only`(实测 20M 字符 21 分钟、零 GLM、深层
  检索正常),仅重点文档用默认语义模式(详见测试报告 §8.8)。
- 图片资源语义任务实测 64–110s,曾超过 CLI 60s 默认超时导致 `--wait` 报
  DEADLINE_EXCEEDED(服务端任务仍会完成)。CLI 顶层 `"timeout": 300`
  (无旗标,`ov config edit` 不含该项;`ov config show` 确认实际生效)
  现由 `bootstrap_auth.py` 生成 `ovcli.conf` 时自动写入(已设置时不覆盖);
  2026-10-26 与 2026-11-01 两次重建靠手工补写的历史不再需要。手工副本
  `ovcli.conf.local` 仍须自行补写/同步。若未来任务更慢仍超时,可继续调高
  该值,或退回不带 `--wait` 提交 + 轮询 `ov task list`。
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

- 配置变更:编辑宿主 `config/ov.conf` → `docker compose restart openviking`
  → 重跑 doctor 与 smoke;不支持热加载。
- 查看日志:`docker compose logs --tail 50 openviking`。排查前先确认输出不
  含密钥;自定义脚本(适配器/bootstrap/smoke)已按"零密钥输出"编写。
- 不运行容器内 `env`/`printenv`,不输出完整 `docker inspect`。

## 备份与恢复

```bash
docker compose stop
TS=$(date +%Y%m%d-%H%M%S)
tar -czf "backups/openviking-${TS}.tar.gz" state config scripts compose.yaml secrets.env
chmod 600 "backups/openviking-${TS}.tar.gz"
docker compose up -d --wait --wait-timeout 180
```

备份含密钥:不打印内容、不上传、不入库。恢复演练必须在独立临时目录、
独立 Compose project name、并在配置副本中显式更换 1933/18353 端口后进行,
不得覆盖或连接在用状态。

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
