# OpenViking 存储模型与检索数据流

> **版本基线**:源码核对基于 `openviking v0.4.4`;机制在 v0.4.5(本仓当前部署版本)一致(v0.4.4 有 role.value 阻塞 bug、v0.4.5 已修,见 `CLAUDE.md` 坑 #11)。源码行号为核对版本、跨版本可能有小偏移,复核以 `grep` 符号定位为准。本仓**手动锁定版本、不自动追 latest**(见 `CLAUDE.md`「运维脚本」)。
> **适用配置**:`embedding.text_source=content_only`、`embedding.max_input_tokens=6144`、本地 Qwen3-Embedding-0.6B(1024 维)+ qwen3-reranker-0.6b。
> 本文档回答:**openviking 怎么存数据、查询时各环节用哪段文本、`max_input_tokens` 的精确影响边界**。

---

## TL;DR

OpenViking **不是**传统 RAG(存文本块、返回文本块),而是把数据分**三层**分离存储,检索 / rerank / 返回都跑在**摘要层**,正文只在存储层:

| 层 | 存什么 | 是否截断 |
|---|---|---|
| **viking_fs**(`viking://` 文件系统) | **完整原文**,一字不漏 | 不截断 |
| **vectordb**(向量库) | 向量 + `abstract`(vlm 摘要) + 元数据(uri 等) | **不含正文** |
| 截断文本(临时) | 只在算向量时用一下 | 算完即弃,**不落盘** |

**一句话**:`max_input_tokens` 只影响**向量召回**;rerank 和返回用的是 `abstract`(vlm 摘要),与正文长度无关;完整正文一直躺在 viking_fs,靠 `read(uri)` 取。

---

## 1. 三层分离的存储模型

### 1.1 viking_fs —— 完整原文(真相之源)

文件入库时,调用方传入的 `content` 被**原样写入** VikingFS(AGFS 后端),没有任何截断。

- `content_write.py:_write_in_place` → `viking_fs.write_file(uri, content)`
- `viking_fs.py` 的 `write_file` 直接 `content.encode("utf-8")` 写盘,无长度处理

**所以 8K 全文(甚至更大的文件)完整存在 viking_fs**,这是后续 `read(uri)` 能取回完整内容的保证。

### 1.2 vectordb —— 向量 + 摘要 + 元数据(没有正文)

vectordb 每条记录的字段由 `collection_schemas.py:context_collection()` 定义:

```
id, uri, type, context_type, vector, sparse_vector,
created_at, updated_at, active_count, level, name, description,
tags, search_tags, abstract, account_id, owner_user_id
```

**关键:没有任何 `content` / `text` / `body` 字段。** 其中:

- `vector` —— 来自正文(经 max_input_tokens 截断后)的 dense 向量
- `sparse_vector` —— 预留的稀疏向量位(当前仅 volcengine/vikingdb provider 会写)
- `abstract` —— **vlm 生成的文件摘要**,不是正文
- `uri` —— 指向 viking_fs 里的完整原文

也就是说,**vectordb 既不存 8K 全文,也不存 6K 截断文本,一段正文都不存**。

### 1.3 截断文本 —— 算完即弃

`max_input_tokens` 截断的那段 6K 文本,生命周期极短:

1. 读完整正文 → 2. `prepare_embedding_input` 按 max_input_tokens 截断 → 3. 送 embedding API → 4. 拿到向量 → **截断文本丢弃**

截断文本**只用于算向量,从不落盘**(不进 vectordb、不进 viking_fs 别处)。

---

## 2. 入库数据流

```
写入 8K 文件
   │
   ├─► viking_fs:完整存 8K 原文 ─────────────────────────► read(uri) 永远拿完整 8K
   │      (content_write.py → viking_fs.write_file,无截断)
   │
   └─► SemanticProcessor 入库管线:
         ① vlm 读正文(上限 semantic.max_file_content_chars=30000 字符)
            → 生成摘要 summary                         [semantic_processor._generate_text_summary]
         ② 读完整正文 → 截断到 6K token(max_input_tokens)
            → embedding → dense 向量                   [base.py:prepare_embedding_input → truncate_embedding_input]
         ③ upsert 到 vectordb:
            {uri, abstract=summary, vector, 元数据...}   [collection_schemas.py upsert]
            ↑ 无正文!截断文本也未保存
```

注意两个独立的"看正文"上限:

- **向量**:看正文前 `max_input_tokens`(6144 token)
- **摘要 abstract**:vlm 看正文前 `max_file_content_chars`(30000 字符,远大于前者)

---

## 3. 查询数据流

```
用户查询 query
   │
   ① 向量召回:query 向量化 → 搜 vectordb
      → 返回 [{uri, abstract, score, ...}]           [RETRIEVAL_OUTPUT_FIELDS,无正文]
   │
   ② rerank:对每条结果的 abstract(vlm 摘要)打分       [hierarchical_retriever 三处都用 r.get("abstract")]
      ↑ 不是正文!不回查 viking_fs
   │
   ③ 返回:[{uri, abstract, score}]                    [_convert_to_matched_contexts → MatchedContext]
      MCP search 结果末尾提示:"Use the read tool to expand a URI"
   │
   ④ 用户 read(uri) → 从 viking_fs 取完整 8K 原文
```

---

## 4. 关键问题逐条解答

> 场景:一段 8K token 文本存入 openviking(content_only + max_input_tokens=6144)。

**Q1 算向量的是不是只有开头 6K?**
✅ 是。读完整 8K → 截断前 6K → embedding → 向量。向量只反映前 6K 语义;截断文本算完即弃。

**Q2 rerank 用的是 6K 还是完整 8K?**
❌ 都不是 —— 是 vlm 生成的**摘要 `abstract`**。三处 rerank 调用全是 `r.get("abstract","")`,对摘要打分,rerank **从不回查 viking_fs 读正文**。

**Q3 返回给用户的是 6K 还是 8K?**
❌ 都不是 —— 返回 `uri + abstract(摘要) + score`,**不含正文**。完整 8K 要 `read(uri)` 自己取。

**Q4 openviking 存完整 8K 还是开头 6K?**
✅ **完整 8K 存 viking_fs**。vectordb 里一段正文都不存(既不存 8K 也不存 6K);6K 那段根本不落盘。

---

## 5. `max_input_tokens` 的影响边界

| 环节 | 用的文本 | 8K 中超出 6K 的后 2K 能用到吗 |
|---|---|---|
| **向量召回** | 正文前 6K 截断后的向量 | ❌ 用不到(被截断,可能漏召回) |
| **rerank** | `abstract`(vlm 摘要) | ➖ 不相关(rerank 不看正文) |
| **返回** | `abstract` + `uri` | ➖ 不相关 |
| **read** | viking_fs 完整 8K | ✅ 完整可用 |

**结论:`max_input_tokens` 只影响"向量召回"一环。** 调大它(如 6K→8K)只提升向量对长文档尾部的召回覆盖,对 rerank 排序和返回内容无任何影响。

---

## 6. 核心洞察:检索跑在摘要层

这是理解 openviking 数据行为的钥匙,也修正一个常见误解:

> "content_only 保留正文细节 → 代码/技术文档检索好" —— **只对了一半**。

实际上:

- **content_only 的"正文细节"只体现在向量召回**(向量来自正文前 6K);
- 文件一旦被召回,**rerank 和返回就已经切换到摘要层**了 —— 排序和返回内容由 `abstract`(vlm 摘要)决定;
- 所以**最终检索质量 = 向量召回(看正文)× 摘要质量(看 vlm)**,两层叠加,`text_source` / `max_input_tokens` 只调第一层。

**推论**:文件尾部的内容(后 2K)——

- 向量召回可能漏(向量只编码了前 6K);
- 但如果 vlm 生成摘要时看到了(30000 字符内),`abstract` 里可能仍然体现,rerank / 返回还能命中。

---

## 7. 配置旋钮作用层速查

不同旋钮作用于不同层,别混淆:

| 旋钮 | 位置 | 作用层 | 影响 |
|---|---|---|---|
| `embedding.text_source` | embedding 段 | 向量来源 | content_only=正文 / summary=摘要,决定向量吃什么文本 |
| `embedding.max_input_tokens` | embedding 段 | 向量召回 | 正文送 embedding 的截断长度(默认 4096,本仓 6144);**只影响召回** |
| `semantic.max_file_content_chars` | semantic 段 | 摘要生成 | vlm 生成 abstract 时看正文的截断长度(默认 30000 字符);影响摘要质量 → 影响 rerank/返回 |
| `embedding.dense.dimension` | embedding.dense | 向量维度 | 改了必须重建 vectordb(见 CLAUDE.md 坑 #4) |
| `embedding.dense.query_param`/`document_param` | embedding.dense | 向量计算 | 非对称 embedding 的 query/document 前缀(见坑 #7) |

---

## 8. 源码依据(v0.4.4)

| 结论 | 文件:行号 |
|---|---|
| 原文完整写入 viking_fs,无截断 | `storage/content_write.py:416-446`、`storage/viking_fs.py:2489-2502` |
| vectordb 字段定义(无 content/text) | `storage/collection_schemas.py:46-90` |
| `Context.to_dict()` 不含正文,vectorize 不入库 | `core/context.py:167-192` |
| 向量召回返回字段(无正文) | `storage/viking_vector_index_backend.py:23-30`(`RETRIEVAL_OUTPUT_FIELDS`) |
| embedding 前按 max_input_tokens 截断 | `models/embedder/base.py:186-212`、`utils/embedding_input.py:19-41` |
| 截断文本不落盘,只算向量后 upsert | `storage/collection_schemas.py:562-568,693` |
| rerank 三处都用 `abstract`(不回查正文) | `retrieve/hierarchical_retriever.py:303,353,481` |
| 返回 `MatchedContext`(uri+abstract+score) | `retrieve/hierarchical_retriever.py:591-600`、`openviking_cli/retrieve/types.py:276` |
| MCP search 返回摘要 + 提示 read | `server/mcp_endpoint.py:267-283` |
| 摘要由 vlm 生成(上限 max_file_content_chars) | `storage/queuefs/semantic_processor.py`(`_generate_text_summary`) |

> 复核方法(版本升级后快速验证本文结论是否仍成立):
> ```bash
> # 确认 vectordb 仍不存正文(看 Fields 有无 content/text)
> docker exec openviking python3 -c "import json;d=json.load(open('/app/workspace/vectordb/context/collection_meta.json'));print([f.get('FieldName') for f in d['Fields']])"
> # 确认 rerank 仍用 abstract
> docker exec openviking grep -n 'abstract' /app/.venv/lib/python3.13/site-packages/openviking/retrieve/hierarchical_retriever.py
> ```
