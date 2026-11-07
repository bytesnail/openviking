# 部署与日常运行(活文档)

> 定位:流程与细则。操作值(端口、身份、镜像 digest 等)以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

## 前提

- 宿主需有 `bash`、`python3`(`prepare.sh` 的 secrets/config 校验与密钥生成)、
  `curl`(认证矩阵)、`ss`(端口检查)、`tar`(备份)与 Docker Compose。
- `secrets.env` 已存在且含非占位 `Z_AI_API_KEY`(缺失时 `prepare.sh` 会明确报错);`OPENVIKING_ROOT_API_KEY` 缺失时由 `prepare.sh` 自动生成 32 字节 hex,手工提供时须非占位且至少 16 字符。
- `model-serving/bin/serve.sh status` 显示两个模型运行(18351/18352 在位)。
- 当前用户 UID/GID 为 `1000:1000`(与 `compose.yaml` 的 `user` 一致;其他用户部署须先显式改 Compose 与 `scripts/prepare.sh` 中的常量)。
- 端口 1933、18353 空闲。
- Docker Engine ≥ 支持 compose `--wait`;镜像 tag+digest 双固定(当前值以
  `compose.yaml` 为准,不拉 latest;tag commit `f5e6203033a1` 与镜像 label
  revision 互证)。

## 首次部署

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

## bootstrap_auth.py 细则

面向容器内执行(默认 `OPENVIKING_CLI_CONFIG_FILE=
/app/.openviking/ovcli.conf`、user key `/app/.openviking/secrets/user_api_key`
均为容器内挂载路径)。宿主直跑只在显式覆盖 `OPENVIKING_CLI_CONFIG_FILE`
与 `OPENVIKING_USER_KEY_FILE` 后才有意义;另可用 `OPENVIKING_BOOTSTRAP_URL`
与 `OV_BIN` 覆盖服务地址与 CLI 可执行名(2026-11-03 检视补记)。
旧 default 配置缺省 `url` 时兼容文档化 loopback 默认值;新临时身份配置
必须显式写 URL。

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

## 适配器运行参数(默认值见 scripts/embedding_adapter.py)

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
  (restart 即生效:单文件 bind 在容器重启时重新解析 inode,2026-11-07 实测;
  旧归档仓"restart 无效须 down/up"说法不实,详见 runtime-behavior.md。)
- 查看日志:`docker compose logs --tail 50 openviking`。排查前先确认输出不
  含密钥;自定义脚本(适配器/bootstrap/smoke)已按"零密钥输出"编写。
- 不运行容器内 `env`/`printenv`,不输出完整 `docker inspect`。
