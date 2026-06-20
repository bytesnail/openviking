# OpenViking 端到端测试方法与记录

> **版本基线**:测试在 `openviking/openviking:v0.4.3` + 本仓当前 ov.conf 上完成(2026-06)。
> **定位**:记录 openviking 端到端测试的**设计思路、实现方式、数据/副作用管控、当前结果、踩过的坑**,以及**升级前复测的可操作 checklist + 可复用脚本**。
> 相关:`STORAGE_MODEL.md`(数据行为)、`KIMI_FOR_CODING.md`(vlm 后端)、`CLAUDE.md` 坑 #8/#11。

---

## TL;DR

- **核心原则:`doctor` / `/health` 不是功能可用性的判据**——它不发 chat、不验入库。必须用真实 MCP 调用走完整业务流程(写入→摘要→向量化→检索→读)。
- **测试方式**:容器内 `docker exec ... python3` 跑"手搓 MCP client"(`httpx` 对 `/mcp` 发 JSON-RPC over SSE),**不走 claude code 的 MCP 集成**,可控、可批量、可拿原始 JSON。
- **覆盖两条入库路径**:memory(经 session extraction)和 resource(经 semantic_processor)——不同代码路径,都要测。
- **本轮在 v0.4.3 + vlm timeout 调整后全通过**;过程中挖出两个 doctor 看不出的问题(v0.4.4 role.value 阻塞 bug、vlm timeout 偏小),都已修。

---

## 1. 测试目标与核心原则

**目标**:确认两件事——① 功能**真的**完整可用(不是 doctor 报 PASS 的假象);② 表现**符合 ov.conf 设置**(content_only / max_input_tokens / temperature / timeout / dense 维度 / reranker 等)。

**核心原则(血泪教训)**:

| 信号 | 能否证明功能可用 |
|---|---|
| `curl /health` 200 | ❌ 只查端口 |
| `doctor` 全 PASS | ❌ 不发 chat、不验入库(坑 #8) |
| 容器 `healthy` | ❌ healthcheck 只查端口(坑 #2) |
| **真实 MCP 写入→检索→读全通** | ✅ 唯一可靠判据 |

openviking 的多个严重问题(v0.4.4 入库全崩、vlm timeout 致摘要空)**doctor 全报 PASS**。所以端到端测试必须真正走业务流程。

---

## 2. 测试架构:手搓 MCP client

**为什么不用 claude code 的 MCP 集成**:可控性。手搓 client 能精确观察每一步、批量跑、循环轮询等异步索引、拿到原始 JSON 分析,且不依赖 claude code 配置。**走的协议/端点/工具和任何 MCP client 完全一致**,所以结论通用。

**实现**(`docker exec -i openviking python3` 跑):

```python
import httpx, json
BASE = "http://127.0.0.1:1933/mcp"
KEY = os.environ["OPENVIKING_ROOT_API_KEY"]
H = {"Authorization": f"Bearer {KEY}",
     "Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}
c = httpx.Client(timeout=300.0)   # ≥ vlm timeout,否则等索引时会自己断
sid = None; _id = 0

def psse(t):                        # 解析 SSE:取最后一个 data: 的 JSON
    L = None
    for l in t.splitlines():
        if l.startswith("data: "):
            try: L = json.loads(l[6:])
            except: pass
    return L

def call(method, params=None, i=None):
    global sid
    b = {"jsonrpc":"2.0","method":method}
    if i is not None: b["id"]=i
    if params is not None: b["params"]=params
    h = dict(H)
    if sid: h["Mcp-Session-Id"] = sid
    r = c.post(BASE, headers=h, json=b)
    if not sid: sid = r.headers.get("mcp-session-id")   # initialize 时拿 session
    return psse(r.text)

def notify(method):
    b={"jsonrpc":"2.0","method":method}
    h=dict(H)
    if sid: h["Mcp-Session-Id"]=sid
    c.post(BASE, headers=h, json=b)

def tool(name, args):
    global _id; _id+=1
    return call("tools/call", {"name":name,"arguments":args}, i=_id)

def txt(j):    # 从 tools/call 结果提取文本
    return "\n".join(x["text"] for x in j["result"]["content"] if x.get("type")=="text")

# 握手
call("initialize", {"protocolVersion":"2024-11-05","capabilities":{},
                    "clientInfo":{"name":"e2e","version":"1"}}, i=1)
notify("notifications/initialized")
# 之后 tool("remember"/"add_resource"/"search"/"read"/"forget", {...})
```

**鉴权**:`X-Api-Key` 或 `Authorization: Bearer <root_api_key>`(见 `server/auth`)。

**用到的 MCP 工具**:`remember`(写 memory)、`add_resource`(写 resource)、`search`(检索)、`read`(读)、`list/ls`(列目录,确认 scope/清理)、`forget`(删,清理)。

---

## 3. 怎么确保"完整全面"

### 3.1 覆盖矩阵(两条入库路径 × 各环节 × 各配置点)

| 维度 | memory 链路 | resource 链路 |
|---|---|---|
| 入库工具 | `remember` | `add_resource`(本地上传) |
| 后台管线 | session `_run_memory_extraction` | `semantic_processor`(parse→summary→overview→vectorize) |
| 验证摘要(vlm 工作) | memory 的 abstract | resource 的 `.abstract.md`/`.overview.md` |
| 验证向量(content_only 来自正文) | 独特关键词召回 | 独特关键词召回 |
| 验证 rerank | 多结果 score 排序 | 多结果 score 排序 |
| 验证 read(完整原文) | read memory uri | read resource 文件 uri |

**为什么两条都要测**:它们走**不同代码路径**。v0.4.4 的 role.value bug 同时断了两条(都在 `ctx.role.value`),但理论上可能只断一条——只测一条会漏。memory 轻(短消息提取)、resource 重(全文 summary + 目录 overview),对 vlm timeout 的压力也不同,分开测才能分别暴露问题(本轮正是 resource 链路先暴露了 timeout 问题)。

### 3.2 异步等待(关键)

入库是**异步**的:remember/add_resource 立即返回,但摘要 + 向量化在后台跑(kimi reasoning 慢,可能 1-5 分钟)。**不能写入后立刻 search**(会空)。必须**轮询**:

```python
t0 = time.time(); ready = False
while time.time()-t0 < 300:                       # 上限 ≥ vlm timeout
    t = txt(tool("search", {"query":"<独特关键词>","target_uri":"<scope>","limit":5,"min_score":0.2}))
    if "Found" in t and "0 item" not in t and "no abstract" not in t:
        ready = True; break
    time.sleep(8)
```

判断"就绪"的标志:能搜到 **且 abstract 非空**(abstract 空说明 summary 还没生成/失败——v0.4.4 bug 和 timeout 问题的共同症状就是 abstract 空)。

### 3.3 验证三维度

每条结果验证:① **抽象**(abstract 非空且语义合理 → vlm 在工作);② **精确**(独特关键词命中 → content_only 向量来自正文);③ **完整**(read(uri) 返回全文 → viking_fs 存完整)。

### 3.4 测试文本设计

- **独特关键词**:用现实中不会出现的标识(`Qubit-7`、`Zephyr-9`、`10.0.0.0/24`、`wal_level=logical`),确保召回 = 命中测试数据,不会和别的内容混淆。
- **多主题**(memory):编辑器偏好 / 项目部署 / 数据库配置 / 无关干扰项 → 验证语义区分 + rerank 把无关项排低。
- **中等长度**(resource):覆盖全文但 < max_input_tokens(不触发截断),专注验证 content_only + summary 链路。

---

## 4. 测试数据与副作用管理

### 4.1 数据生命周期(写→测→清)

```
写入(独特 uri/session) → 轮询等就绪 → 查询/read 验证 → forget 清理 → ls 验证空
```

### 4.2 清理流程

| 数据 | 清理 |
|---|---|
| memory | `forget viking://user/default/memories recursive=True`(测试用 default user,整棵清掉) |
| resource | `forget viking://resources/<name> recursive=True` |
| 容器内临时文件 | `docker exec openviking rm -f /tmp/<test>.md` |
| 宿主临时文件 | `rm -rf /tmp/ovtest`(若起了 http server serve 文件) |

### 4.3 确认无残留

清理后 `list` 对应 scope 应返回空/无测试条目:
```python
txt(tool("list", {"uri":"viking://resources"}))      # 应无 qubit
txt(tool("list", {"uri":"viking://user/default/memories","recursive":True}))  # 应无测试记忆
```

### 4.4 副作用与成本

- **kimi token 消耗**:每次入库都调 vlm 生成 summary/overview(reasoning 模型,不便宜)。resource 链路尤重(文件 summary + 目录 overview 两次+)。测试要有预算意识,别反复灌大数据。
- **default user 污染(Q4,实测确认)**:测试数据写在 `viking://user/default/...`。**无法用独立 user 隔离**——`X-OpenViking-User` header 只在 "trusted mode" 下生效(当前 root_api_key 模式带该 header 直接 `403 X-OpenViking-User can only assert identity in trusted mode`);而 trusted mode 要求 header + URL 都带 account/user 且会信任身份断言(有安全含义),**不为测试开**。所以防污染靠:① 当前 default user 无真实数据时,`forget viking://user/default/memories recursive` 安全;② **将来 default user 有真实数据时,改用"测前 `ls` 快照 + 测后按独特关键词定位、精确 `forget` 测试 uri"**(不 recursive 整树)。
- **vectordb(Q3 澄清)**:`forget` 删除 viking_fs 条目 **+ 对应的向量记录**(`ls`/`search` 全空 = 数据删干净)。vectordb 保留的是 `collection_meta`(schema:字段/维度/索引类型),这是**结构**而非数据——测试不改维度/schema,保留是正常的、**不是残留**,无需重建。

---

## 5. 当前测试结果(v0.4.3 + vlm timeout=1200)

> 前提:已从 v0.4.4 降级 v0.4.3(见 §6.1)、vlm timeout 调到 300(§6.2)。

### 5.1 memory 链路 ✅

写入 5 条消息(编辑器偏好 / Zephyr-9 部署 / PostgreSQL 逻辑复制 / 干扰项)→ 58s extraction 完成 → 3 查询全部精准命中:

| 查询 | 命中 uri | abstract(节选) | score |
|---|---|---|---|
| "我平时用什么编辑器写代码" | `.../memories/preferences/user/开发工具与主题偏好.md` | "主力编辑器为 Vim,明确不使用 Emacs;偏好深色主题" | 100% |
| "Zephyr-9 项目生产网段" | `.../memories/entities/项目/zephyr_9.md` | "生产部署网段 10.0.0.0/24;数据库主节点 IP 10.0.0.7" | 100% |
| "怎么开启 postgresql 逻辑复制" | `.../memories/entities/数据库配置/postgresql_logical_replication.md` | "wal_level=logical;需配 max_replication_slots" | 99% |

证明:memory extraction 成功(vlm 工作=temperature/timeout 对)、向量来自正文(精确关键词召回)、rerank 排序、abstract 质量高。

### 5.2 resource 链路 ✅

`add_resource` 上传 `qubit.md`(量子计算笔记,含 `Qubit-7`/`T2=150微秒` 等)→ 268s 完成 summary + 两级 overview + 向量化 → search 100% 召回、abstract 高质量、read 完整原文:

```
- [resource 100%] viking://resources/qubit/qubit.md
    本文是一份量子计算实验笔记…Qubit-7的T2达150微秒(优于同组平均90微秒),
    T1为80微秒…性能提升源于改进的电容垫片几何结构,退相干主因是1/f通量噪声…
```

证明:resource summarization/overview 成功、content_only 向量来自正文、read 返回完整 markdown(viking_fs)。

### 5.3 配置点符合性

| ov.conf 设置 | 验证方式 | 结果 |
|---|---|---|
| `text_source: content_only` | 独特关键词正文召回 | ✅ |
| `max_input_tokens: 6144` | 中等文件全文覆盖 | ✅ |
| `vlm.temperature: 1` | vlm 生成高质量 abstract | ✅ |
| `vlm.timeout: 1200` / `qp.timeout: 600` | resource summary/overview 不超时 | ✅ |
| `dense.dimension: 1024` | collection_meta + 入库成功 | ✅ |
| `rerank`(本地 qwen3-reranker) | 多结果 100%/99% 排序 | ✅ |

---

## 6. 测试中发现的问题与解决

### 6.1 v0.4.4 role.value 阻塞 bug(降级 v0.4.3)

**现象**:v0.4.4 下 remember/add_resource 都"返回成功",但 search 全空;`forget` 直接报错。

**定位**:`docker logs` 看到 `AttributeError: 'str' object has no attribute 'value'` @ `session.py:1329 "role": self.ctx.role.value`。resource summarization 同款报错。

**根因**:v0.4.4 的 [PR#2709](https://github.com/volcengine/OpenViking/pull/2709) 把 `class Role(str, Enum)` 改成 `class Role(str)`(移除 Enum),但所有 `ctx.role.value` 调用没更新 → memory extraction / resource summarization / forget 全崩 → 无摘要无向量。仅 v0.4.4 受影响。([issue #2718](https://github.com/volcengine/OpenViking/issues/2718),修复 [PR#2728](https://github.com/volcengine/OpenViking/pull/2728) 已合并 main 但未发版。)

**解决**:降级 v0.4.3(ab656e24 还没进,v0.4.3 无此 bug)。**doctor 全程报 PASS,完全是假象。**

### 6.2 vlm timeout 偏短(单位:**秒**;当前 vlm=1200 / qp=600)

**现象**:v0.4.3 默认 timeout=60s 下,memory 成功(58s),但 resource 203s 仍搜不到、abstract 空 `(no abstract)`、search 召回 score 0%。

**定位**:`docker logs` 看到 `Failed to generate summary ... openai.APITimeoutError: Request timed out`(每次正好卡 60s)。文件级 summary + 目录 overview 比 memory extraction 重,kimi reasoning 在 60s 内跑不完 → 超时 → abstract 空 → 向量也没正确建。

**解决**:ov.conf 给 `vlm`/`query_planner` 加 `timeout`(**单位秒**)。先后调过 300/180 → 最终 **vlm=1200 / qp=600**(给 reasoning 留充足余量;max_concurrent 限并发,大 timeout 不增日常成本,只抬最坏上限)。

**实测数据点(Q1:能否按文件大小类推 timeout?)**:

| 文件 | 大小 | summary+overview+向量化耗时 |
|---|---|---|
| qubit.md | ~600 字符 | 268s(首次,含 vlm 冷启动) |
| big.md | 8763 字符 | 120s |

summary/overview 的 vlm 输入上限是 `semantic.max_file_content_chars=30000`(与 embedding 的 `max_input_tokens=6144` 是两码事)。**反直觉:8763 字符比 600 字符还快**——kimi 是 reasoning 模型,耗时由"它决定思考多久"决定、不是输入大小,**不能按文件大小线性类推**。两个数据点都 <300s,但 reasoning 有随机性(qubit 那次 268s 贴近 300),故最终给到 1200 留足余量。

**embedding/rerank 的 timeout(无需配)**:它们在 ov.conf **无可配 timeout 字段**(只有 circuit_breaker 的熔断 timeout),实际请求 timeout **硬编码**——openai rerank=30s、openai embedding 走 SDK 默认。本地 qwen3 秒级响应,够用;限制是硬编码不可配,换远程慢服务时 rerank 30s 可能不够。

**启发**:`VLMConfig.timeout` 默认 60s 对 reasoning 模型偏小;**调 vlm 配置后必须用 resource 链路(重任务)复测**,memory(轻任务)测不出 timeout 问题。

### 6.3 方法层面的踩坑(避免重复)

| 坑 | 现象 | 解法 |
|---|---|---|
| **URI scope 认错** | `ls viking://memory` 报 `Invalid scope`,search 默认搜 `user/default` 搜不到全局 resource | scope 是 `agent/resources/session/user`;resource 在 `viking://resources/<name>`,memory 在 `viking://user/default/memories`;search 加 `target_uri` 指定 |
| **SSRF 防护拦本地 URL** | `add_resource path=http://127.0.0.1:.../file` 被拒(`non-public address`) | 改用**本地文件上传**:先 `add_resource path=/tmp/file` 拿 `temp_upload_signed` URL → `POST` multipart 上传拿 `temp_file_id` → 再 `add_resource temp_file_id=...`。或 ov.conf 顶层 `allow_private_networks: true`(不推荐,降 SSRF 防护) |
| **异步索引没等** | 写入后立刻 search 全空 | 轮询 search 直到"能搜到 + abstract 非空"(§3.2) |
| **read 目录 vs 文件** | `read viking://resources/qubit` 返回 nothing(qubit 是目录) | `ls` 看真实文件 uri 再 read(如 `.../qubit/qubit.md`) |

---

## 7. 可复用测试脚本(附录)

> 直接 `docker exec -i openviking python3 - << 'PYEOF' ... PYEOF` 跑。需先 `source secrets.env` 拿 key(脚本内从容器环境读)。

### 7.1 memory 链路

```python
# 接 §2 的 MCP client helper(init+session) 后:
msgs = [
 {"role":"user","content":"记住:我的主力编辑器是 vim,偏好深色主题,不用 emacs。"},
 {"role":"user","content":"项目 Zephyr-9 生产部署在 10.0.0.0/24,数据库主节点 10.0.0.7。"},
 {"role":"user","content":"PostgreSQL 逻辑复制需 wal_level=logical 并配 max_replication_slots。"},
 {"role":"user","content":"今天周末天气晴朗。"},  # 干扰项
]
print(txt(tool("remember", {"messages":msgs})))
# 轮询等 extraction(§3.2)...
for q in ["我平时用什么编辑器写代码","Zephyr-9 生产网段","怎么开启 postgresql 逻辑复制"]:
    print(txt(tool("search", {"query":q,"limit":5,"min_score":0.1})))
# 清理:
print(txt(tool("forget", {"uri":"viking://user/default/memories","recursive":True})))
```

### 7.2 resource 链路(本地上传)

```python
# 测试文件先写入容器: docker exec -i openviking bash -c 'cat > /tmp/test.md' << 'EOF' ...
r1 = txt(tool("add_resource", {"path":"/tmp/test.md"}))
url = re.search(r'http://[^\s]+temp_upload_signed\?token=\w+', r1).group(0)
with open('/tmp/test.md','rb') as f:
    up = c.post(url, files={"file":("test.md",f,"text/markdown")},
                headers={"Authorization":f"Bearer {KEY}"}, timeout=60)
tfid = up.json()["temp_file_id"]
print(txt(tool("add_resource", {"temp_file_id":tfid})))     # → Resource added: viking://resources/test
# 轮询等 summary+overview+向量化(target_uri=viking://resources)...
print(txt(tool("search", {"query":"<独特关键词>","target_uri":"viking://resources","limit":5,"min_score":0.2})))
print(txt(tool("read", {"uris":"viking://resources/test/test.md"})))   # 完整原文
# 清理:
print(txt(tool("forget", {"uri":"viking://resources/test","recursive":True})))
```

---

## 8. 升级前的端到端测试 checklist

> 每次升级到新版本前(如 v0.4.5 发布后),按此 checklist 走一遍,确认无回归 + ov.conf 无需适配,再正式采用。

### 8.1 步骤

1. **查 release notes / issue**:看新版本是否动过 auth/role/session/semantic_processor/embedding 等核心模块(本轮 bug 就在 auth 重构里);确认上版本的已知 bug 是否已修。
2. **拉镜像 + 起**(用 `./update-openviking.sh <新tag>`):等 healthy。
3. **`doctor`**:连通性探活(仅参考,不是判据)。
4. **memory 链路**(§7.1):remember → 轮询 → search/read。**判据:能召回 + abstract 非空合理。**
5. **resource 链路**(§7.2):add_resource → 轮询 → search/read。**判据:summary/overview 生成(无 timeout)+ search 召回 + read 完整。**
6. **配置点抽查**:独特关键词召回(content_only)、abstract 质量(vlm temperature/timeout)、rerank 排序、read 完整。
7. **清理**:forget 测试数据 + ls 验证空 + 删临时文件。
8. **通过则正式用;有问题 `./update-openviking.sh v0.4.3` 回退**。

### 8.2 重点关注回归点(基于已知坑)

- **auth/role 相关改动** → memory extraction / resource summarization / forget 是否还正常(本轮 v0.4.4 就栽在这)。
- **vlm 调用链** → summary/overview 是否超时(timeout 配置是否仍够)。
- **embedding/vectordb schema** → 维度是否变(变则需重建 vectordb,坑 #4)、索引格式是否兼容。
- **MCP 协议/工具签名** → remember/add_resource/search/read 参数是否变。

### 8.3 红线

- **绝不只看 doctor/healthy 就放行**——本轮两个严重问题它都报 PASS。
- **绝不只测 memory 就放行**——resource 链路更重,是 timeout/summarization 问题的首发地。
- **测完必清理 + 验证无残留**(default user 若有真实数据,改用隔离 uri 或独立测试 user)。

---

## 附:本轮测试的关键命令速查

```bash
# 看 openviking 版本
docker exec openviking openviking-server --version

# 看入库/检索后台日志(排查超时/报错/异步进度)
docker logs openviking --since 10m 2>&1 | grep -iE 'extract|summar|overview|vector|embed|error|fail|exception|timeout' | grep -viE 'GET /health|access -'

# 容器内跑 MCP 测试脚本(见 §7)
docker exec -i openviking python3 - << 'PYEOF' ... PYEOF

# 清理
docker exec openviking rm -f /tmp/test.md
```
