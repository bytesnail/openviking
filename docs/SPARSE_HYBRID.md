# OpenViking Sparse / Hybrid 检索:现状与接入路径

> **版本基线**:源码核对基于 `openviking v0.4.4`;机制在 v0.4.5(本仓当前部署版本)一致(v0.4.4 有 role.value 阻塞 bug、v0.4.5 已修,见 `CLAUDE.md` 坑 #11)。源码行号为核对版本、跨版本可能偏移,复核以 `grep` 为准。本仓手动锁定版本、不自动追 latest(见 `CLAUDE.md`)。
> 定位:讲清 openviking 的 sparse/hybrid **现在能不能用、本地为什么接不进、将来怎么接**。
> 相关:`CONFIG_REFERENCE.md`(provider 矩阵)、`STORAGE_MODEL.md`(检索流程)、`CLAUDE.md` 坑 #10。

---

## TL;DR

- **架构层一直支持** sparse/hybrid(索引类型、`sparse_vector` 字段、Embedder 抽象都就绪);
- **但 provider 实现层目前只有 `volcengine` / `vikingdb`**(火山云);`openai` 的 sparse/hybrid 是 `NotImplementedError`;
- **所以本地开源 sparse(BGE-M3 / SPLADE)现在接不进**;
- 将来 openviking 支持(或自建 provider)后,接入 = 配 sparse 段 + `sparse_weight>0` + 一次 reindex,dense 维度不变可平滑升级。

---

## 1. 为什么需要 sparse

dense 是**语义匹配,不是字面匹配**(见 `STORAGE_MODEL.md`)。对**代码标识符、专有名词、ID、精确短语、罕见术语**,dense 召回是结构弱项——可能召回语义相近但字面不同的,或漏掉字面精确匹配的。

sparse(BM25 / SPLADE 风格的词法稀疏向量)补的就是**精确关键词/词法匹配**。hybrid = dense(语义召回)+ sparse(词法召回)融合,正是"长文档语义 + 代码精确查询"混合场景的理想解。

---

## 2. openviking 对 sparse/hybrid 的架构支持

| 层 | 证据 |
|---|---|
| 索引类型 | Local/Base 用 `flat`/`flat_hybrid`,Qdrant/Volcengine/VikingDB 用 `hnsw`/`hnsw_hybrid`(官方 VectorDB Adapters 文档) |
| 字段 | `collection_meta.json` 有 `SparseVectorKey`(`sparse_vector` 字段,标注来源 "Sparse embedding (BM25/SPLADE)") |
| Embedder 抽象 | 架构图 Embedder 标 `(Dense / Sparse / Hybrid)`;有 `CompositeHybridEmbedder`(把一个 dense embedder + 一个 sparse embedder 组合成 hybrid) |
| 检索融合 | `storage.vectordb.sparse_weight`(默认 0.0,>0 启用 hybrid,用 logit-alpha 融合 dense/sparse 分数) |

**即:数据结构、索引、检索融合逻辑都已就位,缺的只是"能产出 sparse 向量的本地 provider"。**

---

## 3. provider 现状(v0.4.4)

`embedding_config.py` 的工厂注册表(`grep '\("[a-z]+", "(sparse|hybrid)"\)'`):

```
("volcengine", "sparse") / ("volcengine", "hybrid")
("vikingdb",   "sparse") / ("vikingdb",   "hybrid")
```

`openai_embedders.py` 里 `OpenAISparseEmbedder` / `OpenAIHybridEmbedder`:

```python
class OpenAISparseEmbedder(SparseEmbedderBase):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("OpenAI does not support sparse embeddings. ...")
```

**结论:无 `openai` / `local` / `litellm` / `ollama` 的 sparse 实现。** 本地 llama-server 走的是 openai provider → 无法产出 sparse。

---

## 4. 本地开源 sparse 为什么接不进

接入需要 openviking 有一个**能调本地 sparse 服务、产出 sparse 向量的 embedder 实现**。当前没有(见上)。即便你本地起一个 BGE-M3 sparse 服务(OpenAI 兼容接口),openviking 的 openai provider 也只会把它当 dense 用(`/v1/embeddings`),不会走 sparse 路径。

---

## 5. SOTA 开源 sparse 模型(将来接入时参考)

| 模型 | 特点 | 本地部署 |
|---|---|---|
| **BGE-M3**(智源) | **一个模型同时出 dense + sparse + colbert 三路**,MTEB 强 | FlagEmbedding / Infinity / text-embeddings-inference |
| **SPLADE-v3 / SPLADE-v3-Distil**(Navér) | 纯神经 sparse,BEIR sparse 赛道 SOTA | sentence-transformers / TEI(`/embed_sparse`) |
| **BM25**(Lucene/ES/OpenSearch) | 经典词法精确匹配,无语义;非 embedding API 形态 | 任意,但同样接不进 openviking |

> 最理想的是 **BGE-M3**:一次部署三路全有。但它需要 openviking 支持对应的 sparse provider 才能接入。

---

## 6. 三条接入路径(都不轻松)

| 路径 | 做法 | 代价 |
|---|---|---|
| **等官方** | 关注 [volcengine/OpenViking](https://github.com/volcengine/OpenViking) issue/release,等 sparse provider 扩展 | 0 工作量,但时间不可控;可主动提 feature request |
| **魔改源码** | 给 openviking 加一个 `local`/`openai` sparse embedder 实现 + 注册 | 工作量大;**镜像每次 update 会被覆盖**(需 fork 镜像或 volume 覆盖) |
| **云 sparse** | 用 volcengine/vikingdb 的 sparse/hybrid | 数据出本地、付费;违背全本地初衷 |

---

## 7. 将来 provider 到位后怎么配

假设 openviking 支持 `local`/`openai` 的 sparse(或你自建了一个 sparse 服务):

```jsonc
"embedding": {
  "dense":  { ... 现有 qwen3 1024 维 ... },
  "sparse": { "provider":"local", "model":"bge-m3", "api_base":"http://127.0.0.1:PORT/v1" },
  ...
},
"storage": {
  "vectordb": { "name":"context", "backend":"local",
    "sparse_weight": 0.5   // >0 启用 hybrid,融合 dense/sparse;调这个控制两者权重
  }
}
```

- 索引类型自动从 `flat` 升级为 `flat_hybrid`;
- **dense 维度(1024)不变 → 现有 dense 向量不用推倒重来**;
- 旧文件需走一次 **reindex** 补生成 sparse 向量(新文件自动 hybrid);
- 即"配置 + reindex"一次性升级,平滑。

---

## 8. 复核 + 关注动向

```bash
# 当前 sparse provider 支持(哪天多出 openai/local 就是能接了)
docker exec openviking grep -nE '\("[a-z]+", "(sparse|hybrid)"\)' \
  /app/.venv/lib/python3.13/site-packages/openviking_cli/utils/config/embedding_config.py
```

- 官方仓库/issues:[github.com/volcengine/OpenViking](https://github.com/volcengine/OpenViking)
- 相关 issue(均为现有 provider 迁移,非新增 sparse):[#1523](https://github.com/volcengine/OpenViking/issues/1523) embedder 迁移体验、[#1066](https://github.com/volcengine/OpenViking/issues/1066) 模型变更检测
