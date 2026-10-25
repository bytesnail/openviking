# OpenViking v0.5.2 镜像与本机实况核查

核查日期：2026-10-25
对象：`ghcr.io/volcengine/openviking:v0.5.2`（本机已拉取）与部署目标环境
目的：设计文档 `docs/superpowers/specs/2026-10-25-openviking-compose-design.md` 的关键实施假设在“实际将运行的镜像”上逐条核实；与 [OpenViking 官方与本机调研](2026-10-25-openviking-official.md) 互补（该笔记以 GitHub 页面为来源，本笔记以镜像内文件为准）。
凭据纪律：全文无任何凭据值；`secrets.env` 只核对了变量名清单。

## 1. Registry 与镜像元数据

- `docker buildx imagetools inspect`：`v0.5.2` index digest `sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd`，linux/amd64 manifest `sha256:2ae2799d…`——与调研笔记一致。
- 镜像 OCI labels：`org.opencontainers.image.revision=7f3ae1af822d936c1d0b070537772b0f1d59122f`、`version=v0.5.2`——独立印证 tag→commit 映射。
- 镜像 OCI config：`Entrypoint=["openviking-entrypoint"]`、`WorkingDir=/app/.openviking`、`ExposedPorts=1933/tcp`、内置 `HEALTHCHECK CMD openviking-entrypoint --healthcheck`（interval 30s / timeout 5s / retries 3 / start_period 30s，与设计文档 Compose 中的 healthcheck 相同）。
- 默认 env：`OPENVIKING_CONFIG_FILE=/app/.openviking/ov.conf`、`OPENVIKING_CLI_CONFIG_FILE=/app/.openviking/ovcli.conf`、`HOME=/app`、`PATH=/app/.venv/bin:…`。

## 2. 镜像内文件系统事实

- `python` / `python3` 解析到 `/app/.venv/bin/python`（Python 3.13.17），实测 `--version` 可用——适配器“复用同镜像 Python 标准库”的假设成立。
- `/usr/local/bin/openviking-entrypoint` 存在且在 PATH。
- `/opt/openviking` 目录**不存在**；设计中的 `./scripts:/opt/openviking/scripts` 挂载点由 Docker 运行时创建，不依赖镜像内容。
- 镜像默认 USER 为 **root**；`/app`、`/app/.openviking` 属 `root:root` 755。设计的 `user: "1000:1000"` 运行意味着只有挂载的 `state` 可写；`$HOME=/app` 为 root 属主，依赖库写 `$HOME`/缓存路径的行为属实施必须验证项（设计文档 §5 已据此补充）。

## 3. entrypoint 脚本要点（/usr/local/bin/openviking-entrypoint，全文已读）

- `OPENVIKING_WITH_BOT` 默认 `1`；`0/false/no/off` 等归一为关闭；非法值 exit 2。
- `OPENVIKING_SERVER_HOST` 默认 `0.0.0.0`；`OPENVIKING_SERVER_PORT` 优先于 `ov.conf` 的 `server.port`，默认 1933，非法值报错。
- 缺 `ov.conf` 时启动 `openviking-pending-health`（503 提示服务器）轮询等待；支持 `OPENVIKING_CONF_CONTENT` 或 `openviking-server init`。
- `--healthcheck` 即 `curl -fsS http://127.0.0.1:<port>/health`。
- 启动 `openviking-server --host <host> --port <port> [--with-bot]`，等待 `/health` 最多 `OPENVIKING_HEALTH_MAX_ATTEMPTS`（默认 120）次。

## 4. CLI 命令核实（镜像内 help 输出）

| 命令/旗标 | 结论 |
|---|---|
| `openviking-server doctor` | 存在；检查项：Config/Python/Native Engine/AGFS/Authentication/Embedding/VLM/Ollama/VikingBot/Disk，**无 rerank 项** |
| `ov config add custom --name <n> --url <u> --activate` | 存在，且为官方示例原文 |
| `--api-key-env <env>` / `--root-api-key-env <env>` | 存在（另有 `--api-key-stdin` / `--root-api-key-stdin` / `--force`） |
| `ov health`、`ov find`、`ov ls`、`ov add-resource --to … --wait` | 均存在，`-o json` 为全局选项 |
| `ov config validate` | 存在（可用于 §9.1.2 沙盒 schema 校验） |

## 5. venv 内 v0.5.2 源码逐条核实

以下文件均在 `/app/.venv/lib/python3.13/site-packages/{openviking,openviking_cli}/`，与 GitHub tag 同源（OCI revision label 一致）。

| 调研论断 | 证据 | 结论 |
|---|---|---|
| glm provider 默认 `api_base=https://api.z.ai/api/coding/paas/v4` | `openviking/models/vlm/backends/glm_vlm.py:11`；`openviking_cli/setup_wizard.py:37` | ✅ |
| glm provider 默认模型为 `glm-4.6v`（显式指定 `glm-5.3-flash` 必要） | `glm_vlm.py:12` | ✅（新增事实） |
| 全仓无 `[1m]` 后缀解析、无 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | openviking + openviking_cli 全量 grep 零匹配 | ✅ |
| ov.conf 在 `json.loads` 前执行 `os.path.expandvars` | `openviking/config/config_file.py:67`；server 侧 `openviking/server/bootstrap.py:132`；CLI 侧 `openviking_cli/doctor.py:68` | ✅（服务器与 CLI 两侧都展开） |
| `provider:"openai"` 不发送 `dimensions` | `openai_embedders.py:346-351`（`_provider=="openai"` 恒 False）；工厂 `embedding_config.py` 传 `provider="openai"` | ✅（其他 provider 复用该引擎且带 `api_base` 时才发送） |
| `api_base` 存在时允许无 key，用 `no-key` 占位 | `openai_embedders.py:150`；`embedding_config.py` 工厂 `cfg.api_key or "no-key"` | ✅ |
| `input` 字段默认 `"multimodal"` 但**必须显式设置才生效** | `embedding_config.py:48`（默认值）；工厂仅当 `"input" in cfg.model_fields_set` 才传 `input_type` | ✅（新增事实，设计显式设置是必要条件） |
| embedding `max_input_tokens` 默认 4096 | `embedding_config.py:667-670` | ✅ |
| rerank `max_input_tokens` 默认 0；正值须 ≥128 | `rerank_config.py:54-61, 87-88` | ✅（新增约束） |
| rerank `threshold` 默认 0.1 | `rerank_config.py:50-52` | ✅ |
| openai rerank 要求非空 `api_key` + `api_base`；`api_base` 为完整 endpoint | `rerank_config.py:109-111` | ✅ |
| rerank 请求体 `model/query/documents/top_n`；解析顶层 `results` | `openai_rerank.py:82-101`（qwen3 风格） | ✅ |
| rerank 失败/结果异常回退向量分数 | `hierarchical_retriever.py:303-345`（两条回退路径） | ✅ |
| query 占预算 3/4，document 用余量 | `hierarchical_retriever.py:322-325`（`* 3 // 4`） | ✅ |
| vlm `max_concurrent` 默认 32 | `vlm_config.py:201-203` | ✅ |
| vlm `max_retries` 默认 3 | `vlm_config.py:163` | ✅ |
| 未配置 `query_planner` 时复用 VLM 配置 | `open_viking_config.py:242, 681-684` | ✅ |
| 多模态 embeddings input 为裸 typed parts 数组（无 `content` 包装） | `embedding_msg_converter.py:81-92` | ✅ |
| dev 认证仅 loopback 且恒 ROOT | `server/auth/plugins/dev.py:15-54` | ✅ |
| `POST /api/v1/admin/accounts`，响应 `result.user_key` | `server/routers/admin.py:63,450-503`（prefix `/api/v1/admin`）；`result["user_key"]` 由 `should_expose_user_key` 控制（`server/auth/__init__.py:437-444`：仅 `trusted` 模式省略） | ✅（`api_key` 模式下必含） |
| `get_session_context(token_budget=128_000)` 默认 128K | `session/session.py:2750`；`server/routers/sessions.py:509-511` | ✅ |
| doctor embedding 检查发真实请求并验维度 | `openviking_cli/doctor.py:198-268`（`embed_compat("OpenViking doctor embedding probe")`） | ✅ |

未逐行复核（与部署无直接决策关系）：doctor 的 VLM 项“只验配置不发请求”、`/ready` 的 embedding 检查行为——以官方调研笔记的 GitHub 出处为准，另见 GitHub 侧核查笔记。

## 6. 本机环境核对

| 项 | 结果 |
|---|---|
| 宿主用户 | `meteor` uid/gid = 1000:1000（与 Compose `user: "1000:1000"` 一致） |
| 端口 | 18351/18352 仅 `127.0.0.1` 监听（model-serving）；1933/18353/18790 空闲 |
| `secrets.env` | 仅含 `Z_AI_API_KEY`（只核对变量名） |
| `model-serving/bin/serve.sh` | 存在，`status [MODEL]` 子命令可用 |
| model-serving README | 多模态 embeddings 用 `content` 包装；rerank 响应 `results[]`（`index`+`relevance_score` 降序）；768 维；`SERVE_CTX=8192`（每槽 8448 padded）；`/v1/rerank` 支持图文（本地补丁 llama-server） |
| Docker | Engine 29.9.2 linux/amd64；Compose v5.6.1；`--wait`/`--wait-timeout`/`config --quiet`/`exec -T` 旗标均在 |

## 7. 对设计文档的反馈

上述核查未发现设计文档原有论断错误；第 5 节标注“新增事实”的三处（glm 默认模型、`input` 显式设置才生效、rerank 预算 ≥128 约束）及镜像 root/HOME 属主、`/opt/openviking` 不存在、`user_key` 仅 trusted 模式省略等事实，已于 2026-10-25 回写进设计文档 §2.1、§3.2、§5、§6.1、§7.2、§9.1、§9.3。
