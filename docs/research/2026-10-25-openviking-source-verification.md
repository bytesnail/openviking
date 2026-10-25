# OpenViking v0.5.2 源码论断核查

- **核查日期**:2026-10-25
- **核查方式**:源码取自 GitHub 官方 tag 归档 `https://github.com/volcengine/OpenViking/archive/refs/tags/v0.5.2.tar.gz`(与 `raw.githubusercontent.com/volcengine/OpenViking/v0.5.2/<path>` 内容一致),本地解压后逐文件核对行号;Release/Tag 元数据取自 `api.github.com/repos/volcengine/OpenViking`(releases 列表、`git/ref/tags/v0.5.2`、`commits/v0.5.2`、`compare/d164def8...v0.5.2`)。固定 tag v0.5.2,未参考 main。下文行号均对应该 tag 的文件内容,可用 `raw.githubusercontent.com/volcengine/OpenViking/v0.5.2/<路径>#L<行号>` 复核。
- **结论概览**:32 条论断中 31 条 ✅ 确认,1 条(论断 7 中「镜像内存在 /opt/openviking 目录」)❌ 不符;无 ⚠️ 无法验证项。

## A. Release 基线

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 1 | v0.5.2 是最新非 draft/prerelease release(2026-10-23);轻量 tag v0.5.2 指向 commit 7f3ae1af822d936c1d0b070537772b0f1d59122f | ✅ | `api.github.com/repos/volcengine/OpenViking/releases?per_page=20` 首条即 v0.5.2:draft=false、prerelease=false、published_at=2026-10-23T12:43:54Z;`api.github.com/repos/volcengine/OpenViking/git/ref/tags/v0.5.2` 返回 object.sha=7f3ae1af822d936c1d0b070537772b0f1d59122f、object.type="commit"(annotated tag 此处应为 "tag",故为轻量 tag) | 无 |
| 2 | release notes 的 "Release target" 是 d164def87a2a376df6653eab5c206e00109777b1,与真实 tag 不同;compare 显示 tag 比 release target 多两个提交 | ✅ | release body 同时含「发布目标:`d164def87a2a376df6653eab5c206e00109777b1`。」与 "Release target: `d164def…`";`api.github.com/.../compare/d164def8…...v0.5.2` → status="ahead"、ahead_by=2、behind_by=0,两提交为 2260f689(fix(langchain))与 7f3ae1af(fix(release)) | 无 |

## B. Docker / entrypoint

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 3 | WORKDIR /app/.openviking;默认配置 /app/.openviking/ov.conf;CLI 配置 /app/.openviking/ovcli.conf | ✅ | `Dockerfile#L112`(WORKDIR /app/.openviking)、`#L133`(ENV OPENVIKING_CONFIG_FILE=/app/.openviking/ov.conf)、`#L134`(ENV OPENVIKING_CLI_CONFIG_FILE=/app/.openviking/ovcli.conf) | 无 |
| 4 | 缺 ov.conf 时 entrypoint 启动临时 503 health server 并等待 | ✅ | `deploy/docker/openviking-entrypoint.sh#L39-L79`(ensure_config:无 ov.conf 且无 OPENVIKING_CONF_CONTENT 时启动 openviking-pending-health 并 while 循环等待文件出现);`deploy/docker/pending_health_server.py#L52`(send_response(503)),所有路由均回同一 503 JSON(L60-L66) | 无 |
| 5 | 环境变量名:OPENVIKING_SERVER_HOST(默认 0.0.0.0)、OPENVIKING_SERVER_PORT(默认 1933)、OPENVIKING_WITH_BOT(=0 关闭)、OPENVIKING_CONFIG_FILE、OPENVIKING_CLI_CONFIG_FILE、OPENVIKING_CONF_CONTENT | ✅ | entrypoint `#L129`(`SERVER_HOST="${OPENVIKING_SERVER_HOST:-0.0.0.0}"`)、`#L17`/`#L22`(PORT 缺省 1933)、`#L4`+`#L86-L88`(WITH_BOT 默认 1,`0/false/no/off` 均关闭)、`#L6`(CONFIG_FILE)、`#L44-L45`(CONF_CONTENT);CLI 配置 env 定义于 `openviking_cli/utils/config/consts.py#L15-L16`(`OPENVIKING_CONFIG_ENV="OPENVIKING_CONFIG_FILE"`、`OPENVIKING_CLI_CONFIG_ENV="OPENVIKING_CLI_CONFIG_FILE"`) | 无 |
| 6 | healthcheck 为 `openviking-entrypoint --healthcheck`,内部请求 http://127.0.0.1:<port>/health;openviking-entrypoint 安装进 PATH | ✅ | `Dockerfile#L138-L139`(HEALTHCHECK CMD ["openviking-entrypoint","--healthcheck"]);entrypoint `#L96-L98`(`--healthcheck` 分支 `exec curl -fsS http://127.0.0.1:${SERVER_PORT}/health`,URL 拼装在 `#L28`);安装:`Dockerfile#L126`(COPY → /usr/local/bin/openviking-entrypoint)+ `#L129-L130`(chmod +x;/usr/local/bin 在默认 PATH) | 无 |
| 7 | 基础镜像、镜像内系统 python、/opt/openviking 目录、默认 USER | 部分确认 | 最终 runtime 基础镜像 `python:3.13-slim-trixie`(`Dockerfile#L101`;build stages 为 rust:1.91.1-trixie L5、node:24-trixie-slim L8、ghcr.io/astral-sh/uv:python3.13-trixie-slim L20)。系统 `python` 存在:基础镜像为官方 python 镜像(自带 /usr/local/bin/python),且 entrypoint 脚本自身即调用 `python`(entrypoint `#L12`、`#L63`)。无 USER 指令 → 默认 root。**/opt/openviking 不符** | ❌(部分):主镜像内不存在 `/opt/openviking`。主 Dockerfile 无任何 /opt/openviking 引用;源码在 builder 阶段 COPY 到 `/app`(`Dockerfile#L51-L64`),runtime 阶段只有 `/app/.venv`(L114)、`/usr/local/bin/openviking-entrypoint|openviking-pending-health`(L126-L127)与 `/app/.openviking`(L128)。`/opt/openviking` 仅出现在另一个辅助镜像 `deploy/docker/cuvs-dev/Dockerfile#L47`。设计若要跑 `/opt/openviking/scripts/xxx.py`,须在容器内自建该目录(默认 root 可写)或改放其他路径 |
| 8 | 官方 docker-compose.yml 的 openviking service 形态 | ✅ | `docker-compose.yml#L19-L44`:image `ghcr.io/volcengine/openviking:latest`(L22)、ports `"${OPENVIKING_SERVER_PORT:-1933}:${OPENVIKING_SERVER_PORT:-1933}"`(L27)、`volumes: ~/.openviking:/app/.openviking`(L29)、environment 用 `${VAR:-默认}` 透传(L30-L37)、healthcheck `test: ["CMD", "openviking-entrypoint", "--healthcheck"]` + interval 30s/timeout 5s/retries 3/start_period 30s(L38-L43)、restart: unless-stopped(L44);另有 caddy service(L52-L71) | 无 |

## C. 配置加载

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 9 | ov.conf 是 JSON;JSON parse 前执行 os.path.expandvars();未设置的环境变量保留 ${...} 字面值 | ✅ | `openviking_cli/utils/config/config_loader.py#L83-L92`:`open(path, encoding="utf-8-sig")` 读入 → `raw = os.path.expandvars(raw)`(L88)→ `json.loads(raw)`(L92);L86-L87 注释明确 "Unset variables are left unchanged by expandvars()" | 无 |
| 10 | 存在 server.host / server.port / server.auth_mode(值含 "api_key")/ server.root_api_key;storage.workspace 默认 "./data" | ✅ | `openviking/server/config.py#L332`(host,默认 "127.0.0.1")、`#L333`(port,默认 1933)、`#L352`(auth_mode)、`#L353`(root_api_key);`get_effective_auth_mode` `#L393-L405`:root_api_key 非空 → AuthMode.API_KEY,枚举值 "api_key" 见 `openviking/server/identity.py#L72`;ov.conf 顶层 server 节为合法字段(`openviking_cli/utils/config/open_viking_config.py#L571`、`#L576`);`openviking_cli/utils/config/storage_config.py#L42`(workspace 默认 "./data") | 无 |

## D. Embedding

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 11 | 字段 embedding.dense.provider/model/api_key/api_base/dimension/input/encoding_format 与 embedding.max_concurrent/max_retries/max_input_tokens;max_input_tokens 默认 4096 | ✅ | `openviking_cli/utils/config/embedding_config.py`:`EmbeddingConfig.dense` `#L647`;`EmbeddingModelConfig`:model `#L43`、api_key `#L44`、api_base `#L45`、dimension `#L46`、input `#L48`(默认 "multimodal")、provider `#L69`、encoding_format `#L94`(Literal["float","base64"]);max_concurrent `#L654`(默认 10)、max_retries `#L659`(默认 3)、max_input_tokens `#L667-L671`(默认 4096) | 无 |
| 12 | provider "openai":api_base 是 base URL(SDK 拼 /embeddings);不发送 dimensions;有 api_base 时允许无 api_key(内部 "no-key" 占位) | ✅ | `openviking/models/embedder/openai_embedders.py`:api_base 作为 `base_url` 传给 openai SDK(`#L176-L177`,endpoint 由 SDK 拼);`_should_send_dimensions` `#L346-L351`:provider=="openai" 时返回 False(不发送 dimensions;注:其他兼容 provider 配了 api_base 时会发送,论断限定 "openai" 成立);`#L150` `"api_key": self.api_key or "no-key"`;`#L154-L156`(无 api_key 但有 api_base 时不报错),配置层同样规则 `embedding_config.py#L216-L219` | 无 |
| 13 | input "text" 从多模态 parts 提取文本;input "multimodal" 原样传递 parts | ✅ | `openviking/models/embedder/openai_embedders.py#L144`(`_supports_multimodal = input_type == "multimodal"`);`openviking/models/embedder/base.py#L305-L331`:`prepare_embedding_input` 在 supports_multimodal=False 时 `extract_text_from_content(content)`(L328,定义 `#L150`)提取文本并丢弃 image parts;True 时 parts 列表原样保留(仅对 text part 做截断,L313-L326) | 无 |

## E. 多模态 embedding 请求形态

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 14 | 请求 input 是裸 typed parts 数组 [{"type":"text",...},{"type":"image_url",...}],无外层 {"content":[...]} 包装 | ✅ | `openviking/storage/queuefs/embedding_msg_converter.py#L81-L92`:parts 逐个 append `{"type":"text","text":...}` / `{"type":"image_url","image_url":{"url":...}}`,`message = parts`(L90)直接作为 embedding input;`openviking/utils/image_search.py#L95-L105`(build_multimodal_embedding_input 同为裸 parts 数组) | 无 |

## F. Rerank

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 15 | rerank.api_base 必须是完整 endpoint(如 http://host/v1/rerank);provider "openai" 要求非空 api_key 和 api_base | ✅ | `openviking/models/rerank/openai_rerank.py#L60`(docstring "Full endpoint URL for the rerank API")、`#L144`(httpx 直接 POST 到 `url=self.api_base`);`openviking_cli/utils/config/rerank_config.py#L108-L112`(provider=="openai" 且缺 api_key 或 api_base 即 ValueError) | 无 |
| 16 | 字段 timeout/threshold/max_input_tokens/log_payloads;max_input_tokens 默认 0(不截断);为正时 query 最多占预算 3/4、document 用余下,且保留头尾 | ✅ | `rerank_config.py`:timeout `#L42`(默认 30.0)、threshold `#L50`(默认 0.1)、max_input_tokens `#L54-L61`(默认 0,"0 disables truncation";0 或 ≥128,`#L87-L88`)、log_payloads `#L63-L69`;截断:`openviking/retrieve/hierarchical_retriever.py#L319-L327`(`max_query_tokens = rerank_max_input_tokens * 3 // 4`,document 用 `budget - query`);`openviking/utils/token_estimation.py#L56-L94`(`truncate_text_to_token_budget`,head_ratio=0.75,保留头尾加 marker) | 无 |
| 17 | 请求体 model/query/documents/top_n;响应解析顶层 results,按 index 取 relevance_score | ✅ | `openai_rerank.py#L95-L102`(flat body: model/query/documents/`top_n: len(documents)`)、`#L116`(`response_json.get("results")`)、`#L172-L179`(按 `item["index"]` 写回 `relevance_score`,兼容复数 `relevance_scores`)。备注:`#L84-L94` 为 DashScope native API 提供嵌套 envelope 例外(api_base 含特定路径时),标准 OpenAI/Cohere 形态与论断一致 | 无 |
| 18 | rerank 失败回退到向量分数 | ✅ | `hierarchical_retriever.py#L302-L350`:`_rerank_scores` 异常 → logger.warning + return fallback_scores(`#L335-L339`);结果为空或长度不符同样回退(`#L341-L345`) | 无 |

## G. VLM

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 19 | 原生 provider "glm";默认 api_base https://api.z.ai/api/coding/paas/v4;GLMVLM 继承 OpenAIVLM 走 chat.completions.create() | ✅ | `openviking/models/vlm/backends/glm_vlm.py#L11`(DEFAULT_GLM_API_BASE)、`#L24-L28`(`class GLMVLM(OpenAIVLM)`,normalize 后交父类);`openviking/models/vlm/registry.py#L14`("glm" 注册)、`openviking/models/vlm/base.py#L387`;`openviking/models/vlm/backends/openai_vlm.py#L357/L385/L473/L501`(`client.chat.completions.create`) | 无 |
| 20 | 字段 temperature/max_retries/timeout/max_tokens/max_concurrent/extra_headers/extra_request_body/reasoning_effort;max_tokens 只控输出,None 用 provider 默认 | ✅ | `openviking_cli/utils/config/vlm_config.py`:temperature `#L162`(默认 0.0)、max_retries `#L163`(默认 3)、timeout `#L164`、max_tokens `#L194-L197`(Optional,None = provider default,描述 "Maximum tokens for VLM completion output")、max_concurrent `#L201`(默认 32)、extra_headers `#L215`、extra_request_body `#L219`、reasoning_effort `#L227` | 无 |
| 21 | 未配置独立 query_planner 时复用 VLM 配置 | ✅ | `openviking_cli/utils/config/open_viking_config.py#L681-L685`:`get_query_planner()` — query_planner 未配置或无任何配置内容时 `return self.vlm` | 无 |
| 22 | 全仓无 [1m] 后缀解析、无 vlm context_window 字段、无 CLAUDE_CODE_AUTO_COMPACT_WINDOW 引用;get_session_context(token_budget=128_000) 默认 128K | ✅ | `grep -rF "[1m]"`(全仓所有文件)零匹配;`context_window` 在 vlm_config.py / openviking/session/ / openviking/models/ 均无匹配(仅 tests/gateway 的 Policy 字段与 VLM 无关);`CLAUDE_CODE_AUTO_COMPACT_WINDOW` 零匹配(最接近的是 `examples/pi-experimental-context-management/lib/context-window-core.mjs#L51` 的 `AUTO_COMPACT_NOTES` 字符串常量,与该环境变量无关);`openviking/session/session.py#L2750`(`async def get_session_context(self, token_budget: int = 128_000)`) | 无 |

## H. 认证

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 23 | dev 模式仅允许 loopback 但所有请求获得 ROOT | ✅ | `openviking/server/auth/plugins/dev.py`:`_LOCALHOST_HOSTS = {"127.0.0.1","localhost","::1"}`(`#L15`);`resolve_identity` 恒返回 `Role.ROOT`(`#L39-L44`);`validate_config` 非 localhost 时报错并 `sys.exit(1)`(`#L46-L64`) | 无 |
| 24 | api_key 模式:root key 用于账户管理;tenant 数据 API 需单独 User/Admin key;root key 不能直接访问 tenant 数据 API | ✅ | `docs/en/guides/04-authentication.md#L25`("uses a root key for account administration and user/admin keys for tenant data access")、`#L50`(Root Key → ROOT,Account management)、`#L158`(tenant-scoped API 须 USER 或 ADMIN key)、`#L162-L165`("A ROOT key is not bound to a tenant user, so it cannot access tenant-scoped data APIs in api_key mode") | 无 |
| 25 | POST /api/v1/admin/accounts,body {"account_id","admin_user_id"},响应含 result.user_key | ✅ | `openviking/server/routers/admin.py`:`router = APIRouter(prefix="/api/v1/admin", …)` `#L63`;`@router.post("/accounts")` + `@require_auth_root` `#L450-L451`;`CreateAccountRequest` `#L66-L73`(account_id/admin_user_id 必填,"default"/"default" 合法);响应 `#L498-L504`:`Response(status="ok", result={"account_id","admin_user_id","user_key"})`;user_key 在 api_key 模式下必然包含(`should_expose_user_key`,`openviking/server/auth/__init__.py#L437-L444`:非 trusted → True;仅 trusted 模式不暴露)。文档示例同结构:`docs/en/guides/04-authentication.md#L61-L65` | 无 |

## I. CLI

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 26 | 健康诊断命令 `openviking-server doctor` | ✅ | `openviking_cli/server_bootstrap.py#L84-L88`(argv[1]=="doctor" → `openviking_cli.doctor.main`;pyproject.toml#L230 定义 openviking-server 入口)。补充:`ov doctor` 同样存在(`openviking_cli/rust_cli.py#L53-L56`,Python-native 子命令,与 openviking-server doctor 调用同一 doctor.py),两者皆可用 | 无 |
| 27 | `ov config add custom --name --url --activate -o json` 旗标;--api-key-env / --root-api-key-env 存在 | ✅ | `crates/ov_cli/src/main.rs`:ConfigAddTarget::Custom `#L2335-L2336`;`ConfigAddCustomArgs`:--name `#L2375`、--url `#L2378`、--api-key-env `#L2388-L2394`、--root-api-key-env `#L2402-L2409`、--activate `#L2419-L2420`;-o 为全局输出旗标(`#L174-L182`,`short, long, global = true`,值 table\|json) | 无 |
| 28 | `ov health -o json` 存在且输出含 healthy 字段 | ✅ | `main.rs#L1329-L1330`(Commands::Health,"Quick health check");`crates/ov_cli/src/health_ui.rs#L32`(`payload.get("healthy").and_then(Value::as_bool)`);JSON 输出走全局 -o json | 无 |
| 29 | `ov add-resource <path> --to viking://... --wait`、`ov find`、`ov ls` 存在 | ✅ | `main.rs`:Commands::AddResource `#L366-L367`(path `#L368-L374`)、--to `#L403-L405`、--wait `#L433-L435`;Commands::Ls `#L500-L502`;Commands::Find `#L876-L877` | 无 |
| 30 | doctor:embedding 发真实请求并验证维度;VLM 只验配置和 key 存在;无 rerank 检查项 | ✅ | `openviking_cli/doctor.py`:embedding 探针 `#L198-L268`(`embed_compat(embedder, "OpenViking doctor embedding probe", is_query=True)` `#L233` 发真实请求;`#L260-L268` 维度不匹配判失败);`check_vlm` `#L342-L404`(仅 model_validate 配置 + 检查 provider/api_key 存在,无网络请求);`_CHECKS` `#L675-L686`(Config/Python/Native Engine/AGFS/Authentication/Embedding/VLM/Ollama/VikingBot/Disk,无 Rerank) | 无 |
| 31 | /health 是 liveness;/ready 的 embedding 检查不发推理请求 | ✅ | `openviking/server/routers/system.py`:`/health` `#L49-L94`(直接返回 status/healthy/version,不做任何子系统探测);`/ready` `#L97-L145+`(仅探测 AGFS ls、VectorDB health_check、APIKeyManager 加载,无 embedding provider 调用) | 无 |

## J. Bot

| # | 论断 | 结论 | 证据 | 差异/更正 |
|---|------|------|------|-----------|
| 32 | 默认 Gateway 127.0.0.1:18790;api_key 模式下 Bot 需要 User/Admin key;默认 sandbox backend "direct" 且 allow_exec=true | ✅ | `bot/vikingbot/config/schema.py`:GatewayConfig host 默认 "127.0.0.1" `#L609`、port 默认 18790 `#L610`;`bot/README.md#L85`(认证时须提供 caller 的 User/Admin API Key)、`#L319`(bot.gateway.host 默认表);allow_exec 默认 True `#L802`;sandbox.backend 默认 `SandboxBackend.DIRECT`("direct",`#L36` 枚举、`#L881` 默认值) | 无 |

## ❌ 与 ⚠️ 汇总

**❌ 不符(1 项)**

- **论断 7(部分)**:官方 openviking 主镜像内**不存在** `/opt/openviking` 目录。主 `Dockerfile`(runtime stage)只有 `/app/.venv`、`/usr/local/bin/openviking-entrypoint`、`/usr/local/bin/openviking-pending-health`、`/app/.openviking`;`/opt/openviking` 只在辅助镜像 `deploy/docker/cuvs-dev/Dockerfile#L47` 中出现。该论断其余子项(基础镜像 `python:3.13-slim-trixie`、系统 `python` 可执行文件存在、默认 USER 为 root)均属实。**对设计的影响**:计划放到 `/opt/openviking/scripts/` 的脚本需要在容器启动时自行创建目录(默认 root,可写),或改用 compose 挂载路径。

**⚠️ 无法验证(0 项)**

- 无。所有 32 条论断均取得一手源码或 API 证据。
