# OpenViking 配置参考(ov.conf)

> **版本基线**:源码核对基于 `openviking v0.4.4`;机制在 v0.4.3(本仓当前部署版本)一致。源码行号为核对版本、跨版本可能偏移,复核以 `grep` 为准。本仓手动锁定版本、不自动追 latest(见 `CLAUDE.md`「运维脚本」)。
> 定位:调 `ov.conf` 时查**字段 / 默认值 / provider 支持 / `extra:forbid` 约束**的速查表。
> 配置陷阱见 `CLAUDE.md`「关键坑」;数据存储/检索行为见 `STORAGE_MODEL.md`。

---

## 关键默认值速查(最容易踩的)

| 字段 | 默认 | 备注 |
|---|---|---|
| `vlm.temperature` / `query_planner.temperature` | `0.0` | **kimi-for-coding 只收 1**,必须显式覆盖(坑 #8) |
| `KimiVLM max_tokens`(未设时) | `32768` | reasoning 会吃预算,偏小(坑 #9) |
| `vlm.thinking` | `false` | 对 kimi **无效**(kimi 强制 reasoning),语义上设 true |
| `embedding.text_source` | `content_only` | summary_first≡summary_only(坑 #10) |
| `embedding.max_input_tokens` | `4096` | content_only 长文档只取头部;只影响向量召回 |
| `embedding.max_concurrent` | `10` | |
| `embedding.dense.input` | `multimodal` | 本地纯文本用 `text` |
| `embedding.dense.provider` | `volcengine` | 本地 llama-server 用 `openai`(坑 #6) |
| `rerank.threshold` | `0.1` | 相关性阈值,可调 |
| `vlm.max_concurrent` / `query_planner.max_concurrent` | `64` | |

---

## embedding 段

### 顶层(`EmbeddingConfig`,`extra:"forbid"`)

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `dense` | `EmbeddingModelConfig` | 自动填本地 bge-small-zh-v1.5-f16 | dense 向量配置(本仓显式配 Qwen3,不走默认) |
| `sparse` | `EmbeddingModelConfig` | None | 稀疏向量(**仅 volcengine/vikingdb**,见 SPARSE_HYBRID.md) |
| `hybrid` | `EmbeddingModelConfig` | None | 单模型同时出 dense+sparse(**仅 volcengine/vikingdb**) |
| `circuit_breaker` | object | failure_threshold=5 / reset_timeout=60 / max_reset_timeout=600 | embedding 熔断 |
| `max_concurrent` | int | 10 | 并发 embedding 请求数 |
| `max_retries` | int | 3 | provider 调用重试(0 禁用) |
| `text_source` | str | `content_only` | 文件向量化文本来源(见 STORAGE_MODEL.md) |
| `max_input_tokens` | int | 4096(≥100) | 正文送 embedding 的截断长度(**只影响向量召回**) |

### dense 子段(`EmbeddingModelConfig`,`extra:"forbid"`)

| 字段 | 默认 | 说明 |
|---|---|---|
| `model` / `provider` | — / `volcengine` | **必填** |
| `api_base` / `api_key` | None | openai provider 用 api_base 即可,api_key 自动占位 |
| `dimension` | None | 不设则按 provider+model 推断;**改了必须重建 vectordb**(坑 #4) |
| `batch_size` | 32 | 批量 embedding 批次 |
| `input` | `multimodal` | 本地纯文本设 `text` |
| `query_param` / `document_param` | None | 非对称 embedding(坑 #7);支持 `"query"` 简单式或 `"k=v,k=v"` 复合式 |
| `encoding_format` | None | `float`/`base64`(openai/azure) |
| `model_path` / `cache_dir` | None | provider=`local` 时用 |
| `ak`/`sk`/`region`/`host`/`version` | None | vikingdb 专用 |
| `enable_fusion`/`res_level`/`max_video_frames` | None | dashscope 多模态 |

---

## rerank 段(`RerankConfig`,`extra:"forbid"`)

| 字段 | 默认 | 说明 |
|---|---|---|
| `provider` | None | volcengine/vikingdb/cohere/openai/litellm |
| `model_name` | `doubao-seed-rerank` | |
| `model_version` | `251028` | |
| `model` | None | 模型标识(openai provider 用) |
| `api_base` | None | **完整 endpoint URL**(如 `http://127.0.0.1:8022/v1/rerank`),非 base(坑 #5) |
| `api_key` | None | 本地服务用占位 `no-key` |
| `threshold` | 0.1 | 相关性阈值 |
| `extra_headers` | None | |
| `ak`/`sk`/`host` | None | vikingdb 专用 |

> **rerank 无 max_input / 截断字段**;document 复用 embedding 那段文本,自动跟随 max_input_tokens。

---

## vlm / query_planner 段(`VLMConfig`,`extra:"forbid"`)

两段用同一 schema;query_planner 未配/空时 fallback 到 vlm。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `provider` | str | None | kimi/openai/...(见 KIMI_FOR_CODING.md) |
| `model` | str | None | **必填** |
| `api_base` / `api_key` | str | None | |
| `temperature` | float | **0.0** | kimi 必须 1 |
| `max_tokens` | int | None(KimiVLM 兜底 32768) | reasoning 模型要留余量 |
| `thinking` | bool | false | **仅 openai+DashScope 生效**;kimi 无效 |
| `max_concurrent` | int | 64 | 并发 LLM 调用 |
| `timeout` | float | 60.0 | 单请求超时(秒) |
| `max_retries` | int | 3 | |
| `stream` | bool | false | |
| `extra_headers` | dict | None | kimi 自动加 `User-Agent: KimiCLI/1.30.0` |
| `extra_request_body` | dict | None | 透传给 provider 的额外请求体字段 |
| `backup` / `providers` / `default_provider` / `api_version` / `forward_api_key` | | | 多 provider/故障切换/Azure |

---

## provider 支持矩阵(v0.4.4)

| 能力 | 支持的 provider |
|---|---|
| **dense embedding** | openai / azure / volcengine / vikingdb / jina / ollama / gemini / voyage / dashscope / minimax / cohere / litellm / **local** |
| **sparse embedding** | **仅 volcengine / vikingdb**(openai 的 sparse 实现 `NotImplementedError`) |
| **hybrid embedding** | **仅 volcengine / vikingdb**(同上) |
| **vlm** | kimi / openai / ...(KimiVLM / OpenAIVLM) |
| **rerank** | volcengine / vikingdb / cohere / openai / litellm |

> 本地开源 sparse(BGE-M3/SPLADE)目前接不进,详见 `SPARSE_HYBRID.md`。

---

## `extra:"forbid"` 警示(加错字段 → 容器起不来)

以下段/子段都是 `extra:"forbid"`,写未定义字段会 pydantic 校验失败:

- `embedding`(顶层)、`embedding.dense`/`.sparse`/`.hybrid`(子段)
- `rerank`(**不支持 max_concurrent**,坑 #3)
- `vlm`、`query_planner`

> 排查起不来:先看 `docker logs openviking` 有无 pydantic `extra forbidden` 报错。

---

## 本仓当前 ov.conf 实际值

```jsonc
"embedding": {
  "dense": { "provider":"openai", "api_base":"http://127.0.0.1:8021/v1",
    "model":"Qwen3-Embedding-0.6B.i1-Q6_K.gguf", "dimension":1024, "input":"text",
    "query_param":"query", "document_param":"document" },
  "text_source": "content_only", "max_input_tokens": 6144, "max_concurrent": 10
}
"rerank": { "provider":"openai", "api_base":"http://127.0.0.1:8022/v1/rerank",
  "api_key":"no-key", "model":"qwen3-reranker-0.6b-q8_0.gguf" }
"vlm":          { "provider":"kimi", "api_base":"https://api.kimi.com/coding/v1",
  "model":"kimi-for-coding", "temperature":1, "max_tokens":131072, "thinking":true, "max_concurrent":10 }
"query_planner":{ ...同 vlm,但 "max_tokens":65536 }
```

---

## 复核命令(版本升级后验证字段是否变)

```bash
# VLMConfig 字段
docker exec openviking grep -nE 'Field\(' /app/.venv/lib/python3.13/site-packages/openviking_cli/utils/config/vlm_config.py
# sparse/hybrid provider 注册
docker exec openviking grep -nE '\("[a-z]+", "(sparse|hybrid)"\)' /app/.venv/lib/python3.13/site-packages/openviking_cli/utils/config/embedding_config.py
```
