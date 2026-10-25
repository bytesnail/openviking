# OpenViking v0.5.2 本机 Docker Compose 部署

本机单用户部署:OpenViking v0.5.2(镜像 tag+digest 固定)复用
`/mnt/usr/tools/model-serving` 的 `embeddinggemma-2`(127.0.0.1:18351)与
`qwen3-vl-reranker-2b`(127.0.0.1:18352),VLM 走 Z.AI GLM Coding Plan
`glm-5.3-flash`,多模态 embedding 经 loopback 适配器(127.0.0.1:18353)。

设计文档(决策依据、参数理由、失败处理):`docs/superpowers/specs/2026-10-25-openviking-compose-design.md`。

## 边界

- 一切服务只监听 `127.0.0.1`(API 1933、适配器 18353),无 `ports` 映射、无反向代理。
- 认证为 `api_key` 模式:root key 管理账户,User key 访问数据;两者分离。
- 不启用 VikingBot(无 18790 Gateway)。
- 真实凭据只在 `secrets.env`(600)与容器环境;`ov.conf` 仅保存 `${VAR}` 引用。

## 前提

- `model-serving/bin/serve.sh status` 显示两个模型运行(18351/18352 在位)。
- 当前用户 UID/GID 为 `1000:1000`(与 `compose.yaml` 的 `user` 一致;其他用户部署须先显式改 Compose 与 `scripts/prepare.sh` 中的常量)。
- 端口 1933、18353 空闲。
- Docker Engine ≥ 支持 compose `--wait`、镜像
  `ghcr.io/volcengine/openviking:v0.5.2@sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd`。

## 部署(首次)

```bash
cd /mnt/usr/tools/openviking
./scripts/prepare.sh
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose exec -T openviking python -I -B /opt/openviking/scripts/bootstrap_auth.py
```

`prepare.sh` 幂等,可重复执行;`secrets.env` 中 `OPENVIKING_ROOT_API_KEY`
缺失时自动生成(32 字节 hex),存在但为空时直接失败。

### 认证凭据位置(敏感,勿粘贴到任何对话/issue)

- root key:`secrets.env` 的 `OPENVIKING_ROOT_API_KEY`(容器环境注入)。
- User key:`state/secrets/user_api_key`(600)。
- CLI 配置:`state/ovcli.conf`(600,含密钥)。

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
  Coding Plan 用量;`ov health -o json` 须含 `"healthy": true`。
- 端到端(§9.4):

```bash
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/deployment-smoke --wait
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/red.png --to viking://resources/deployment-smoke-image --wait
docker compose exec -T openviking ov find "紫鸢尾-7 号信标的校准码"
docker compose exec -T openviking ov ls viking://resources
```

冒烟资源保留供检查;确认 URI 后可单独删除,不做自动递归清理。

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
