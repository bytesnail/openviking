# 2026-11-01 reranker 换型适配验证

## 背景

model-serving 于 2026-10-29 将生产 reranker 从多模态 `qwen3-vl-reranker-2b`(补丁构建)换为纯文本 `qwen3-reranker-0.6b`(stock llama-server,f16 默认,端口仍 18352),2026-11-01 又将 embeddinggemma-2 主干/mmproj 升为 f16 档。本仓 `config/ov.conf` 的 `rerank.model` 仍指向旧名,需同步适配。

## 影响面分析(改前确认)

1. **多模态能力失去无实际影响**:OpenViking v0.5.2 的 rerank 客户端(`openviking/models/rerank/openai_rerank.py`)签名即 `query: str, documents: List[str]`,只发送文本;旧 2B 的图文 rerank 能力 OpenViking 从未使用。
2. **模型名改名安全**:请求封套按 `api_base` 路径判断(`_uses_nested_envelope` 仅匹配 DashScope 原生 `/api/v1/services/rerank`),本机走 flat body(`{"model", "query", "documents"}`);`model` 字段仅透传,llama-server 按已加载模型服务,不校验该名。
3. **threshold=0.1 无需重校准**:该值是 OpenViking 官方默认,原本未按 2B 校准。注意分数分布有变、**过滤行为并非一致**:2B 时代同一冒烟探针 irrelevant=0.376(2026-10-26 实测,越过 0.1 阈值被保留),0.6B 实测 irrelevant≈0.0001~0.000(被过滤),THINKING 模式 rerank 候选集合可能较 2B 时代收窄——这是 0.6B 判别力更接近二值所致,属模型升级的预期行为而非配置缺陷;阈值区分度反而更好,故维持 0.1 不动。
4. **timeout=60 保留**:设计理由(给图文 rerank 留余量)虽已失效,但纯文本 0.6B 8K p50≈0.6s,宽限无害,不做无谓变更。
5. **embedding 侧无配置变更**:模型名 `embeddinggemma-2`、端口链路(适配器 18353 → 上游 18351)均不变。主干 q8→f16 升精度导致向量漂移 cos(q8,f16)≈0.99967(量化误差级),存量向量与新查询向量分属两代权重的排序影响可忽略,不重建索引。

## 变更

| 文件 | 变更 |
|---|---|
| `config/ov.conf` | `rerank.model`: `qwen3-vl-reranker-2b` → `qwen3-reranker-0.6b` |
| `scripts/prepare.sh` | 端口检查报错文案同步新模型名 |
| `README.md` | 拓扑描述更新,注明换型历史与"仅发文本、无功能影响"结论 |

带日期的历史档案(`docs/research/`、`docs/superpowers/specs/`、`docs/testing/` 既有文档)按惯例不改。`run/qwen3-vl-reranker-2b.lock` 残留由用户当日清理(属 model-serving 侧)。

## 验证(2026-11-01 实跑)

- `docker compose restart openviking` 后:
  - `smoke_models.py`:`[vlm-text] OK`、`[vlm-image] OK mentions_red=True`、`[rerank] OK relevant=1.000 irrelevant=0.000`,全部通过。
  - `openviking-server doctor`:Embedding/VLM/Disk 等 All checks passed。
  - `curl /health`:`{"status":"ok","healthy":true,"version":"v0.5.2"}`;`/ready` 各项 ok。
- 冒烟走的是 OpenViking 真实调用路径(flat body + 文本 query/documents),即换型后实际生产行为。
