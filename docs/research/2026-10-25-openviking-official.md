# OpenViking v0.5.2 Docker Compose 部署调研

调研日期：2026-10-25
目标：为本机单用户 OpenViking 部署确定 release 基线、准确配置 schema、模型协议兼容性、认证边界和验收要求。
约束：复用 `/mnt/usr/tools/model-serving` 的 embedding/reranker；VLM 使用 Z.AI GLM Coding Plan；不得披露任何凭据。

## 1. Release 与镜像基线

- 官方仓库：<https://github.com/volcengine/OpenViking>
- 官方文档：<https://docs.openviking.ai/>
- 最新核心 release：**v0.5.2**，发布于 **2026-10-23T12:43:54Z**，非 draft/prerelease。
  - Release：<https://github.com/volcengine/OpenViking/releases/tag/v0.5.2>
  - Latest API：<https://api.github.com/repos/volcengine/OpenViking/releases/latest>
- 轻量 tag 实际指向 commit **`7f3ae1af822d936c1d0b070537772b0f1d59122f`**：
  - <https://github.com/volcengine/OpenViking/commit/7f3ae1af822d936c1d0b070537772b0f1d59122f>
  - <https://api.github.com/repos/volcengine/OpenViking/git/ref/tags/v0.5.2>
- Release notes 中的 “Release target” 是另一个 commit `d164def87a2a376df6653eab5c206e00109777b1`。官方 compare 显示实际 tag 多两个提交；本调研以真实 tag `v0.5.2` / `7f3ae1a…` 为准：
  <https://github.com/volcengine/OpenViking/compare/d164def87a2a376df6653eab5c206e00109777b1...7f3ae1af822d936c1d0b070537772b0f1d59122f>
- 通过 `docker buildx imagetools inspect` 直接核实 registry：
  - `ghcr.io/volcengine/openviking:v0.5.2` manifest digest：`sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd`
  - linux/amd64 manifest：`sha256:2ae2799dac1e606218d9ffeb06af3b65c8218b3556555620a369ed81b7c5aa1f`
  - 调研时 `latest` 与 `v0.5.2` 指向同一 manifest digest；部署仍固定 tag + digest，不使用浮动 `latest`。

## 2. Docker 启动与持久化事实

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/Dockerfile#L105-L149>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/deploy/docker/openviking-entrypoint.sh#L1-L157>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/docker-compose.yml#L18-L71>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/docs/en/guides/03-deployment.md>

已确认：

1. 工作目录是 `/app/.openviking`；默认 `storage.workspace: "./data"` 解析为 `/app/.openviking/data`。
2. 默认配置路径为 `/app/.openviking/ov.conf`，客户端配置为 `/app/.openviking/ovcli.conf`。
3. 缺少 `ov.conf` 时 entrypoint 启动临时 503 health server 并等待；可通过挂载文件或 `OPENVIKING_CONF_CONTENT` 提供配置。
4. entrypoint 默认 `OPENVIKING_SERVER_HOST=0.0.0.0`，可用环境变量覆盖为 `127.0.0.1`；默认端口 1933。
5. 官方 healthcheck 是 `openviking-entrypoint --healthcheck`，请求 `http://127.0.0.1:<port>/health`。
6. 镜像默认启用 VikingBot；`OPENVIKING_WITH_BOT=0` 或 `--without-bot` 可关闭。
7. 官方 Compose 的 Caddy 是 legacy 代理，保留 1934 给既有部署；新部署可以直接使用 1933。
8. `network_mode: host` 下不使用 Compose `ports`；进程直接绑定宿主机网络命名空间中的地址。
9. 老镜像可能把 `./data` 放在 `/app/data`，不在新默认挂载内。当前目录是全新部署，没有既有 OpenViking workspace 需要迁移。

## 3. `ov.conf` 加载与密钥引用

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/config_loader.py#L67-L94>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/open_viking_config.py#L791-L817>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/config/config_file.py#L63-L88>

配置文件是 JSON。加载器在 JSON parse 前执行 `os.path.expandvars()`，因此可以写：

```json
{
  "vlm": {
    "api_key": "${Z_AI_API_KEY}"
  },
  "server": {
    "root_api_key": "${OPENVIKING_ROOT_API_KEY}"
  }
}
```

这会避免在 `ov.conf` 中保存真实密钥。Compose 的 `env_file` 可把变量注入容器环境。

注意：`expandvars()` 对未设置变量保留原样，所以缺少环境变量时，字符串 `${Z_AI_API_KEY}` 可能被当成非空字面值。部署 preflight 必须显式检查变量存在且非空，不能只依赖 schema 或 doctor。展开发生在 JSON parse 前，含 JSON 特殊字符的密钥也必须经过实际配置解析检查。

## 4. 三类模型的准确配置与请求行为

### 4.1 Embedding

配置来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/embedding_config.py#L40-L117>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/embedding_config.py#L635-L717>

请求来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/embedder/openai_embedders.py#L74-L207>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/embedder/openai_embedders.py#L324-L344>

关键字段：`embedding.dense.provider/model/api_key/api_base/dimension/input/encoding_format`，以及 `embedding.max_concurrent/max_retries/max_input_tokens/text_source`。

- `provider: "openai"` 使用 OpenAI SDK，`api_base` 是 base URL，例如 `http://127.0.0.1:18351/v1`，SDK 请求 `/embeddings`。
- 本地服务可不提供真实 key；代码在 `api_base` 存在时允许无 key，并用 `no-key` 占位。显式非秘密占位值也可。
- `encoding_format: "float"` 要求 JSON float 数组，符合本机已验证响应。
- `provider: "openai"` 不发送 `dimensions` 参数；配置的 `dimension` 用于本地维度管理和校验，避免 llama.cpp 收到不支持的参数。
- `input: "text"` 会从多模态 parts 提取文本；`input: "multimodal"` 会原样传递 parts。
- `embedding.max_input_tokens` 默认 4096，是估算文本预算，不是模型 tokenizer 的精确限制。

### 4.2 Reranker

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/rerank_config.py#L8-L137>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/rerank/openai_rerank.py#L40-L207>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/retrieve/hierarchical_retriever.py#L302-L350>

关键字段：`rerank.provider/model/api_key/api_base/timeout/threshold/max_input_tokens/log_payloads`。

- `provider: "openai"` 要求非空 `api_key` 和 `api_base`；本地占位 key 即可。
- **`api_base` 是完整 rerank endpoint**，本机应为 `http://127.0.0.1:18352/v1/rerank`，不是仅 `/v1`。
- 请求体是 `model/query/documents/top_n`；解析顶层 `results`，按 `index` 读取 `relevance_score`。
- rerank 失败会回退到向量分数，所以搜索成功不能证明 reranker 正常，必须做直接 provider smoke。
- `max_input_tokens: 0` 默认不截断。设为正值后，query 最多占预算的 3/4，document 使用剩余预算；保留头尾。
- `log_payloads` 默认 false，应保持关闭，避免记录敏感 query/document。

### 4.3 GLM VLM

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/vlm/backends/glm_vlm.py#L1-L28>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/vlm/backends/openai_vlm.py#L276-L401>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/utils/config/vlm_config.py#L139-L253>

v0.5.2 有原生 `provider: "glm"`：

- 默认 `api_base` 就是 `https://api.z.ai/api/coding/paas/v4`。
- `GLMVLM` 继承 `OpenAIVLM`，调用 `chat.completions.create()`。
- 关键字段：`vlm.provider/model/api_key/api_base/temperature/max_retries/timeout/max_tokens/max_concurrent/extra_headers/extra_request_body/reasoning_effort`。
- `max_tokens` 是输出上限；`None` 使用 provider 默认值。
- 未配置独立 `query_planner` 时，会复用 VLM 配置。

## 5. `[1m]` 与上下文处理的源码结论

对 v0.5.2 全仓检索，没有发现 `[1m]` 或 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 的处理逻辑。

1. VLM 配置没有 `context_window` 字段。
2. OpenAI/GLM backend 把 `model` 字符串原样写入 Chat Completions 请求，不解析或删除后缀。
3. `max_tokens` 只控制输出，不开启 1M 输入。
4. 没有统一的 VLM 1M context 管理器；各调用点自行构造 prompt 和预算。
5. 资源理解示例：
   - 单图周边上下文截为 500 字符；
   - unified analysis 的 `content_preview` 截为 2000 字符，text-only 路径为 3000 字符；
   - 默认每次最多 10 张图、20 个 section。
   来源：<https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/parse/vlm.py#L42-L84>、<https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/parse/vlm.py#L350-L488>
6. Session context 组装是另一套机制：`get_session_context(token_budget=128_000)`，默认 128K。它是返回给调用者的上下文预算，不是 VLM 模型识别或自动 compaction 配置。
   来源：<https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/session/session.py#L2750-L2784>

因此，`glm-5.3-flash[1m]` 若使用，只是交由 Z.AI 解析的模型 ID，不代表 OpenViking 自动管理 1M。用户已选择改用 Z.AI 通用 OpenAI 兼容工具文档中的 `glm-5.3-flash`。

## 6. 多模态 embedding 协议实测

### OpenViking 发出的形态

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/storage/queuefs/embedding_msg_converter.py#L81-L92>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/utils/image_search.py#L95-L105>

```json
{
  "input": [
    {"type": "text", "text": "a red square"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]
}
```

### 本机 `model-serving` 文档验证的形态

`/mnt/usr/tools/model-serving/README.md` 的 `/v1/embeddings` 多模态示例使用：

```json
{
  "input": [
    {
      "content": [
        {"type": "text", "text": "a red square"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }
  ]
}
```

### 2026-10-25 本机实测

使用同一个有效 64×64 PNG、`model: "embeddinggemma-2"`、`encoding_format: "float"`：

| 用例 | HTTP | 结果 |
|---|---:|---|
| 纯文本 | 200 | 1 个 768 维向量 |
| OpenViking parts 原样发送 | **400** | `prompt elements must be a string ...` |
| 文档中的 `content` 包装 | 200 | 1 个 768 维向量，88 prompt tokens |

结论：当前 OpenViking v0.5.2 与本机 llama.cpp 的多模态请求外壳不兼容，直接设置 `input: "multimodal"` 不可用于图片资源。

用户已选择增加 Compose 管理的 loopback 适配器。适配器必须：

1. 仅对非空、全部为受支持 typed parts 的多模态 `input` 增加外层 `{"content": parts}` 包装；文本和文本批处理原样转发。
2. 对多模态响应要求恰好一个向量，否则返回 502，防止静默只取第一项。
3. 仅监听 `127.0.0.1`，不读取 GLM 密钥，不记录请求图片或文档内容。
4. 通过文本回归、协议单元测试、真实图片向量与排序测试后才能启用。

## 7. 本机模型服务实况

来源：`/mnt/usr/tools/model-serving/AGENTS.md`、`README.md`、`run/*.json`，以及只读状态和 HTTP 请求。

- `embeddinggemma-2`：`127.0.0.1:18351`，`POST /v1/embeddings`，q8/gpu，GPU0，`parallel=2`，CTX 8192。
- `qwen3-vl-reranker-2b`：`127.0.0.1:18352`，`POST /v1/rerank`，q8/gpu，GPU1，KV q8_0，`parallel=2`，CTX 8192。
- 两个服务均只监听 loopback 且无鉴权；不应改成 `0.0.0.0`。
- 模型名 `embeddinggemma-2` 和 `qwen3-vl-reranker-2b` 已分别在实际请求中验证可用。
- 文本 embedding 实测 768 维；rerank 实测返回按 `index` 对应的 `relevance_score`。
- 本机 Docker 是 linux/x86_64、rootful 默认 context；Compose v5.6.1，Engine 29.9.2。
- 1933、18353、18790 在调研时均空闲；18351/18352 正在监听。

## 8. 认证、Bot 与网络边界

### Dev 与 API key

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/server/auth/plugins/dev.py#L23-L71>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/docs/en/guides/04-authentication.md#L25-L73>

- dev 只允许 loopback，但所有请求都获得 ROOT；它不能防止本机其他进程或其他 host-network 容器访问。
- `api_key` 模式需要 `server.root_api_key` 做账户管理；tenant 数据访问需要单独 User/Admin key。
- 可用 root key 调 `POST /api/v1/admin/accounts` 创建 default account 和 default admin，响应包含 `user_key`。该响应属于凭据，自动化必须写入权限 600 的文件，不得打印。
- root key 不能直接用于 tenant 数据 API。
- 用户已选择 `api_key` 加固。

### VikingBot

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/bot/README.md#L1-L150>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/bot/vikingbot/config/schema.py#L606-L613>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/bot/vikingbot/config/schema.py#L798-L883>

VikingBot 是多渠道 Agent/Gateway，提供聊天、文件/shell/web 工具、Skills、子代理和 OpenViking 记忆集成。它不是核心 RAG 服务的依赖。

- 默认继承根 VLM，会增加 GLM 调用。
- 默认 Gateway 为 `127.0.0.1:18790`。
- API key 模式下需要 User/Admin key，root key 不是数据凭据。
- 默认 sandbox backend 是 `direct`，`allow_exec=true`；OpenSandbox 是显式可选功能，有额外镜像和资源需求。
- 用户已选择本部署不启用 Bot。

## 9. Z.AI 官方资料

来源：

- Coding Plan endpoint：<https://docs.z.ai/devpack/quick-start>
- 模型切换与 `[1m]`：<https://docs.z.ai/devpack/latest-model>
- GLM-5.3-Flash：<https://docs.z.ai/guides/vlm/glm-5.3-flash>
- Chat Completions：<https://docs.z.ai/api-reference/llm/chat-completion>
- 支持工具：<https://docs.z.ai/devpack/tool/others>
- Usage policy：<https://docs.z.ai/devpack/usage-policy>

已确认：

- Coding Plan 的 OpenAI Chat Completions base URL 是 `https://api.z.ai/api/coding/paas/v4`，不能使用普通 `https://api.z.ai/api/paas/v4` 代替。
- GLM-5.3-Flash 支持文本/图像等输入，模型文档列出 1M context 和 128K max output。
- 通用 OpenAI 兼容工具文档使用 `glm-5.3-flash` 并单独设置工具上下文窗口。
- `glm-5.3-flash[1m]` 出现在 Claude Code 的 1M 配置说明中，同时要求 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`；通用 Chat Completions model enum 未列出该后缀。
- Coding Plan 文档限制订阅用于官方支持的工具/产品；当前列表未列 OpenViking。OpenViking 原生支持 `glm` provider 不等于 Z.AI 官方把 OpenViking 纳入支持范围。使用条款风险必须在方案中保留；严格遵循条款时应改用合适的 Standard API 产品，而不是假设 Coding Plan 可用。

## 10. 验收手段的真实覆盖范围

来源：

- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/doctor.py#L187-L404>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking_cli/doctor.py#L675-L735>
- <https://github.com/volcengine/OpenViking/blob/v0.5.2/docs/en/getting-started/02-quickstart.md#L65-L98>

- `doctor` 的 Embedding 检查会发真实请求并验证维度。
- `doctor` 的 VLM 检查只验证配置和 key 是否存在，**不会发 VLM 请求**。
- doctor 没有 rerank 检查项。
- `/health` 是 liveness；`/ready` 的 embedding 检查也不发 provider 请求。二者不能证明模型凭据或推理正常。
- 必须增加直接 VLM 文本/图像 smoke、直接 rerank smoke，以及 `ov add-resource --wait` + `ov find` 端到端验证。
- VLM 解析路径存在错误后的 placeholder/fallback，rerank 也存在 vector-score fallback，因此仅导入/搜索成功仍不足以证明所有模型正常。

## 11. 尚未执行的事项

- 未拉取或启动 OpenViking 容器；只查询了 registry manifest。
- 未调用 Z.AI，未消耗 Coding Plan 配额，未输出或使用明文密钥进行测试。
- 多模态适配器、API key 自动化初始化和 smoke 脚本尚未实现。
- 尚未进行 OpenViking 端到端启动、Studio、导入、检索、备份或升级测试。
