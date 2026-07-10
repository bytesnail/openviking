# OpenViking 升级计划:从 v0.4.5 往后

> **制定日期**:2026-07-11
> **当前版本**:`openviking/openviking:v0.4.5`(2026-06-24 发布)
> **本仓部署形态**:docker、host 网络(仅本机 `127.0.0.1:1933`)、**local** vectordb backend(dim=1024 cosine)、本机 qwen3-embedding(@8021)+ qwen3-reranker(@8022)、远程 kimi-for-coding vlm、account=default / user=hermes、**已迁移到 0.4 user/peer 模型、无 legacy `viking://agent` 数据**。
> **配套文档**:`CLAUDE.md`(坑#1–#13)、`docs/E2E_TESTING.md`(§8 升级前端到端 checklist + 可复用脚本)、`docs/UPGRADE_0.4.md`(0.3→0.4 迁移)。

---

## TL;DR — 核心结论

1. **存储兼容性:0.4.x 系列内部平滑,无破坏性变更。** v0.4.5 → v0.4.9 可一路直接升级,**无需数据迁移、无需重建 vectordb、无需中间版本跳板**。v0.4.6 引入的 **VikingFS git-backed snapshot 是纯 opt-in 附加功能,不是底层存储引擎替换**(设计文档明确"不修改 `content_write.py`/`viking_fs.write` 等核心写链路")。**→ "版本差距过大无法直接升级"的担忧不成立。**

2. **但发现两个 open 回归阻塞升级**(存在于所有候选版本 v0.4.6–v0.4.9):
   - **[#3134](https://github.com/volcengine/OpenViking/issues/3134)**(v0.4.6 引入):`search`/`find` 丢失约 90% 的 **L2 文档级**命中,auto-recall 注入率从 100% 掉到 22%。根因在 `hierarchical_retriever.py`(**与向量后端无关,local 同样受影响**)。**本仓 memory 正是 L2 文档级(`viking://user/hermes/memories/*.md`)→ 升级后召回会塌陷。** 修复 PR [#3135](https://github.com/volcengine/OpenViking/pull/3135) 仍 open。
   - **[#3101](https://github.com/volcengine/OpenViking/issues/3101)**(v0.4.8 引入):StreamableHTTP 关闭时 SEGV/ABRT,日志 `Failed to flush/close index default` —— **index flush 被打断**。与本仓 2026-07-10 遭遇的索引损坏(`manager_meta.json` 写空)**机制同源**;v0.4.5 无此 bug,升级到 v0.4.8+ 会**新增**这条"shutdown 打断 index flush"的崩溃路径。

3. **推荐决策:暂留 v0.4.5,不要现在升级。** 理由:① 你升级的动机(避免无法升级)已被第 1 条化解;② 升级的代价(检索塌陷 + 损坏风险叠加)远大于收益;③ 版本差距不是障碍,将来可直接跳到修复版。

4. **将来升级的触发条件**:`#3135`(修 #3134)合并进某个正式版本 + `#3101` 有修复 + 该版本发布满 1–2 周无新回归 → 届时按本文档 §6–§9 流程升级(可直接跳版本,无需补升中间版)。

5. **若坚持现在升级**:目标选 **v0.4.9**(候选中风险最低,含 local backend 关键修复;v0.4.8 严格劣于 v0.4.9),**但必须**先做 §6.2 的 `#3134` 基线对比测试、接受 `#3101` 风险、备好 §9 回滚。

---

## 1. 升级动机审视

升级动机原话:"避免后续版本差距过大且底层机制有大的破坏性变更无法直接升级"。

**核实结论:这个担忧不成立。** 见 §2——0.4.x 系列底层存储(AGFS/VikingFS 文件层、vectordb local collection 格式、memory 记录格式)在 v0.4.5→v0.4.9 **完全兼容**,VikingFS git-backed 只是 opt-in 附加层。即便现在不升,将来也能从 v0.4.5 一步跳到 v0.4.10+ 或更高,**不存在"差距过大无法升级"的风险**。

因此,**升级不应由"怕以后升不动"驱动**,而应看"新版本是否带来本仓需要的修复/特性、且不引入阻塞回归"。按这个标准,当前四个候选版本都被 #3134 阻塞(见 §4),没有现在升级的必要。

---

## 2. 存储兼容性分析(结论:可平滑升级,无破坏)

> 证据来源:各版本 release notes + `docs/design/git-version-control-design.md` + `docs/zh/concepts/05-storage.md` + 迁移文档 `docs/zh/migration/01-user-peer-model.md`。

### 2.1 VikingFS git-backed snapshot(v0.4.6)= 纯 opt-in,非底层替换

三条硬证据:
- 设计文档 §1.2 明确:"**不修改** `content_write.py`、`viking_fs.write/rm/mv` 等核心写链路,仅在 VikingFS 上增加 3 个新方法";"不引入隐式 hook,避免影响现有写链路的延迟与一致性语义"。
- snapshot 数据存独立 `.ovgit/` 目录,**不通过 `viking://` 暴露**,与 `vectordb/`/`viking/`/`bot/` 完全隔离;设计文档 §3.3/§4.2 把向量索引文件、embedding cache 排除在 snapshot 之外。
- 设计文档 §1.3 非目标:"不支持向量索引数据的版本化"。

升级到 v0.4.6+ 后,workspace 下会多出一个**惰性的** `.ovgit/{account_id}/` 目录(不自动 commit、不扫描、不插写路径)。若不想要,ov.conf 设 `"git": {"enabled": false}` + down/up,或直接 `rm -rf workspace/.ovgit`。

### 2.2 vectordb local collection 格式 = 兼容

| 版本 | 相关变更 | 对 local backend |
|---|---|---|
| v0.4.6 | 修复"旧 collection schema 兼容" | ✅ 正面,更适配旧数据 |
| v0.4.7 | Volcengine/VikingDB 云后端需新增 `content`/`search_tags` 字段 | ⚪ 仅云后端,local 不涉及 |
| v0.4.8 | cuVS GPU 后端(opt-in) | ⚪ release notes 原文"默认 local CPU 索引不变" |
| v0.4.9 | `fix(vectordb): only write content field for VikingDB backends`(#3114) | ✅ 反证 local 格式没加新字段 |

`workspace/vectordb/context/` 下的 `collection_meta.json`(dim=1024 cosine)+ `index/default/versions/<时间戳>/` + `store/`(LevelDB)在 v0.4.9 可直接读取,**无需重建**。

### 2.3 memory.version(v0.4.8 废弃)= 抽取流水线,非存储格式

`memory.version` 选的是 `SessionCompressor` 版本(V2=ExtractLoop / V3=patch-merge),**产出物底层都是 AGFS markdown 文件 + 同一 collection schema 的向量记录**。v0.4.8 起总走 v3,但:
- 旧记忆(V2 或 V3 抽取的)照常可读可检索;
- `memory.version` 旧配置"仍可加载"(不报错,被忽略);
- **无需 reindex 或数据迁移**;若发现旧记忆检索有重复/偏离,可选 reindex(v0.4.8 后 reindex 更干净,#3077)。

### 2.4 强制迁移 = 无

v0.4.6 release notes 的"0.3.x 先升 0.4.5 再升 0.4.6"跳板要求,**仅针对仍有 legacy `viking://agent/<id>` 或 `viking://session/` 数据的部署**。本仓已在 v0.4.5 完成 user/peer 迁移、用 `viking://user/hermes`、无 legacy 数据 → **满足"直接升级"条件,从 v0.4.5 到任意 0.4.x+ 全程无强制迁移、无跳板**。

---

## 3. v0.4.5 → v0.4.9 各版本速览

| 版本 | 发布 | 重点新特性 / 修复 | 对本仓价值 |
|---|---|---|---|
| **v0.4.5**(本仓) | 06-24 | role.value 修复(坑#11) | — |
| v0.4.6 | 06-29 | VikingFS snapshot(多版本)、整站 sitemap/RSS 导入、VikingDB BM25 grep、共享 skills、**search 不再用 agent_id 当 peer selector** | snapshot 可做 VikingFS 层备份(但需配 .ovgit 后端);BM25 仅云后端 |
| v0.4.7 | 07-02 | MCP compact description(降 token)、本地文件 temp_upload 自动入库、加密写入双路径锁、QueueFS 锁复用修复、TUI setup wizard、**vlm 省略未设默认 max_tokens**(#2946) | #2946 对 kimi 坑#9 友好;其余影响小 |
| v0.4.8 | 07-08 | cuVS GPU 后端(opt-in)、递归网页爬取、**记忆 v3 成唯一**、流式 patch-merge 记忆、插件读 ovcli.conf/OPENVIKING_*、bot extras 合并、**glob 改标准相对路径语义**、**reindex 不再 chunk memory**(#3077) | cuVS 会与 qwen3 抢 GPU;#3077 是实打实修复 |
| v0.4.9 | 07-10 | image search、workspace peer mode、`serialize local collection lazy load`(#3080,修 RocksDB 启动噪声 #3069)、**local 不再写 content 字段**(#3114,修 #2967 大文件 65535 限制)、memory scope 修复、cuVS 优化、SSRF 安全修复 | #3080/#3114 是 local backend 实修复;image search 新能力;**但仅 1 天龄、无正式 release notes** |

### 3.1 break change 对本仓的影响(逐条)

| 版本 | break change | 影响本仓? | 说明 |
|---|---|---|---|
| v0.4.6 | search/find 不再把 `agent_id`/`agent_uri` 当 peer selector | **否** | 本仓用 `viking://user/hermes` + user key,不依赖 agent_id 做 peer 选择 |
| v0.4.6 | `viking://agent/skills` 恢复为共享 skill 目录 | **否** | 无 legacy agent 数据,默认装 `viking://user/hermes/skills` |
| v0.4.7 | Volcengine/VikingDB 云后端需加 `content`/`search_tags` 字段 | **否** | 本仓是 local backend |
| v0.4.8 | `memory.version` 废弃/忽略 | **否** | 旧配置可加载,旧记忆可读(§2.3) |
| v0.4.8 | bot extras(`[bot-full]` 等)合并进 `[bot]` | **否** | 本仓用 docker 镜像,不涉及 pip extras |
| **v0.4.8** | **glob 改标准相对路径语义** | **需注意** | `*.md` 现在只匹配当前目录(非递归),`**/*.md` 才递归。若 MCP client/脚本依赖旧语义,需改 pattern。**这是唯一需要行为调整的点** |
| v0.4.8 | OpenClaw 插件要求 ≥ `2026.5.27` | **否** | 本仓未用 OpenClaw 插件 |

> 结论:**配置层唯一需调整的是 v0.4.8 glob 语义**(且仅当用到 glob 工具时)。ov.conf 的 embedding/rerank/vlm/local vectordb 配置在 0.4.x 全程有效,坑#1–#13 仍然成立。

---

## 4. 风险评估:所有候选版本都被 #3134 阻塞

### 4.1 阻塞级回归(已核实)

| Issue | 标题 | 引入版本 | 状态 | 对本仓影响 |
|---|---|---|---|---|
| **[#3134](https://github.com/volcengine/OpenViking/issues/3134)** | search/find 丢 ~90% L2 文档级命中,auto-recall 注入率 100%→22% | v0.4.6(`hierarchical_retriever.py` 重构) | **open**(PR [#3135](https://github.com/volcengine/OpenViking/pull/3135) open) | **阻塞**:本仓 memory 是 L2 文档级,升级后召回塌陷。根因在检索逻辑层、与后端无关,local 同样受影响 |
| **[#3101](https://github.com/volcengine/OpenViking/issues/3101)** | StreamableHTTP shutdown 时 SEGV/ABRT,`Failed to flush/close index default` | v0.4.8 | **open**(无修复 PR) | **高风险**:index flush 被打断,与本次索引损坏同源;v0.4.5 无此 bug,升级到 v0.4.8+ 会新增这条崩溃路径 |

### 4.2 其他未修回归(继承进 v0.4.9)

| Issue | 标题 | 状态 | 对本仓 |
|---|---|---|---|
| [#2989](https://github.com/volcengine/OpenViking/issues/2989) | search/find 把原始 `messages.jsonl` 排到真实 memory 之上 | open(PR [#3018](https://github.com/volcengine/OpenViking/pull/3018) open) | 中-高,检索质量 |
| [#3136](https://github.com/volcengine/OpenViking/issues/3136) | `.abstract.md` 内容全是 `# Working Memory` 标题而非真实摘要 | open(PR [#3137](https://github.com/volcengine/OpenViking/pull/3137)/[#3140](https://github.com/volcengine/OpenViking/pull/3140) open) | 中-高,召回质量 |
| [#3095](https://github.com/volcengine/OpenViking/issues/3095) | MCP session 结束 → server exit 0 | open | 中,本仓 `restart: unless-stopped` 下每次 MCP 断开触发容器重启 |
| [#3023](https://github.com/volcengine/OpenViking/issues/3023) | 重启后 in-flight 任务永久 "running" | open | 中,运维 |
| [#3096](https://github.com/volcengine/OpenViking/issues/3096) | reindex 后 owner 丢失 | open | 中,若做 reindex |

### 4.3 local vectordb 长期坑(所有版本都还在,升级不解决)

| Issue | 标题 | 状态 |
|---|---|---|
| [#2118](https://github.com/volcengine/OpenViking/issues/2118) | local vectordb 首次索引不落盘,`_recover()` 跳过空索引 → 重启后 search 返空 | open(自 0.3.17,修复 PR #2644 closed 未合) |
| [#1381](https://github.com/volcengine/OpenViking/issues/1381) | `vectordb/context` 脏状态:已导入+索引,但 search/find 返空 | open(自 0.3.5,修复 PR #2603 closed 未合) |
| [#3064](https://github.com/volcengine/OpenViking/issues/3064) | `viking_fs.rm()` 路径不存在时不删子向量条目(orphan) | open(PR [#3070](https://github.com/volcengine/OpenViking/pull/3070) open) |

> #2118/#1381 正是本仓 2026-07-10 故障时踩到的同类现象("删 index 重建后 search 返空"——见坑#13)。这两个修复 PR 都被 close 未合,**升级到任何候选版本都不会修好,反而新版本改动更多、触发概率可能上升**。

### 4.4 各版本风险评级

| 版本 | 评级 | 理由 |
|---|---|---|
| **v0.4.5(现状)** | **最低** | 无 #3134/#2989/#3101。已端到端验证、稳定运行 |
| v0.4.9 | 中(候选中最低) | 继承 v0.4.8 全部回归,但多了 local 实修复(#3080/#3114);无版本专属回归报告;唯一额外风险=仅 1 天龄、soak 最短 |
| v0.4.6 | 中 | 仅 #3134 一个回归 + agent_id 收口(不影响本仓),但缺 local 修复 |
| v0.4.7 | 中-高 | #3134 + #2989 + #3023,且缺 local 修复 |
| **v0.4.8** | **最高** | 累积 5 个未修回归(#3101 SEGV + #3095 + #3136 + #3134 + #2989),且严格劣于 v0.4.9(同回归、少修复)。**绝不要选 v0.4.8** |

---

## 5. 目标版本选择

### 5.1 主推荐:暂留 v0.4.5

- 本仓核心是**检索**(MCP context DB),#3134 直接破坏它;
- 本仓刚因**索引文件损坏**栽过一次,#3101 同源、升级会叠加风险;
- 升级动机(避免无法升级)已被 §2 化解——**版本差距不是障碍**。

### 5.2 将来升级的触发条件(满足后再升)

1. **#3135(#3134 修复)合并**进某个正式版本;
2. **#3101 有修复**(或确认不影响 local + StreamableHTTP);
3. 该版本**发布满 1–2 周**,社区无新阻塞回归报告;
4. 届时按本文档 §6–§9 流程升级,**可直接跳版本**(无需补升中间版)。

### 5.3 若现在非升不可:目标 v0.4.9(不要 v0.4.8)

v0.4.9 = v0.4.8 的回归集合 + 更多 local 修复,**严格优于 v0.4.8**。v0.4.6/v0.4.7 既缺 local 修复又照样背 #3134,没有选它们的理由。但必须:
- 先做 §6.2 `#3134` 基线对比(升级前记录召回,升级后验证;若命中,接受检索降级或应用 #3134 的临时 patch / 回退);
- 接受 #3101 风险(异常退出可能再次损坏 index,靠多版本快照+delta 兜底 + §6.1 备份);
- 备好 §9 回滚。

---

## 6. 升级前准备(checklist)

> 即使决定暂留,也建议**现在就做 §6.1 备份 + §6.2 基线测试**,作为故障(如本次)的应急基线。

### 6.1 备份(必做,坑#13 头号教训)

```bash
# workspace 由容器以 root 写,用临时容器以 root 备份(同坑#4/#13 权限处理)
docker run --rm -v "$(pwd)/workspace:/app/workspace" \
  --entrypoint sh openviking/openviking:v0.4.5 -c \
  'cp -a /app/workspace/vectordb /app/workspace/vectordb.bak-$(date +%Y%m%d) && \
   cp -a /app/workspace/viking   /app/workspace/viking.bak-$(date +%Y%m%d)'
# 配置(ov.conf 已进 git;secrets.env 不进 git,单独拷一份到 gitignore 的备份位)
cp secrets.env backups/secrets.env.bak-$(date +%Y%m%d)
```
> 备份位建议放 `backups/`(已 gitignore)。备份校验:`du -sh workspace/*.bak-*`。

### 6.2 #3134 检索基线测试(升级前记录,升级后对比)

```bash
# 用 hermes 的 user key,跑若干代表性 query,记录"结果数 + top score"
HERMES_KEY="<hermes user key>"   # 见 secrets 或 admin API: GET /api/v1/admin/accounts/default/users
B=http://127.0.0.1:1933
for q in "memory storage" "OpenViking 记忆管理" "<你的真实检索词>"; do
  echo "=== query: $q ==="
  curl -s -X POST "$B/api/v1/search/search" -H "X-API-Key: $HERMES_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\",\"limit\":10,\"score_threshold\":0.1}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin)['result'];print('total:',d.get('total'));[print(' ',round(i.get('score',0),3),i.get('uri','')) for c in ['memories','resources','skills'] for i in (d.get(c) or [])[:3]]"
done | tee backups/search-baseline-v0.4.5-$(date +%Y%m%d).txt
```
**判据**:升级后用同样 query 重跑,若 `total` / top score 大幅下降(如 6→1、0.97→0.0004)→ 命中 #3134,该版本不可用,回退。

### 6.3 ov.conf / 配置预检

- `memory.version` 字段:有则记下(v0.4.8 起被忽略,保留无害);
- 若升 v0.4.8+:确认有没有用 glob 工具依赖 `*.md` 递归语义(§3.1),有则改 `**/*.md`;
- 确认 MCP client 配置不依赖 `agent_id` 做 peer selector(v0.4.6 变更);
- `secrets.env` 的 `${OPENVIKING_ROOT_API_KEY}` / `${OPENVIKING_VLM_API_KEY}` 占位符不动(坑#2)。

### 6.4 镜像与回滚保险

- 确认本地仍有 `openviking/openviking:v0.4.5` 镜像(`docker images openviking/openviking`);升级后**不要**立刻 `docker rmi v0.4.5`(回滚保险,同 CLAUDE.md 保留 v0.4.3 的做法)。

---

## 7. 升级流程

```bash
# 1. 锁 tag 升级(脚本:pull → sed 改 compose image → up -d → wait_healthy → doctor → 清悬空镜像)
./scripts/update-openviking.sh v0.4.9     # 目标版本(§5)

# 2. 版本确认
docker exec openviking openviking-server --version

# 3. 基础健康(坑#1:配置变更必须 down/up;此处镜像变更等同)
curl -fsS http://127.0.0.1:1933/health
ss -tlnp | grep 1933                      # 坑#12:确认仍 bind 127.0.0.1
```

> `update-openviking.sh` 已内置:前置检查(secrets.env 存在、compose 可用)、flock 并发互斥、先 pull 后 sed(避免半破坏状态)、轮询 healthy、doctor 探活、日志轮转。doctor 失败只告警不退出。

---

## 8. 升级后验证(必做 —— 绝不只看 doctor/healthy)

> 红线(坑#8/#11/#13):doctor PASS ≠ 可用。必须端到端。

### 8.1 连通性与 bind
```bash
curl -fsS http://127.0.0.1:1933/health                       # healthy:true
ss -tlnp | grep 1933                                          # 127.0.0.1:1933(坑#12)
docker exec openviking env | grep OPENVIKING                  # 密钥已注入(坑#2)
```

### 8.2 vlm 真实可用性(坑#8 专属判据)
```bash
docker exec -i openviking python3 -c "import asyncio;from openviking_cli.utils.config import get_openviking_config as g;c=g();print(asyncio.run(c.vlm.get_completion_async('1+1=?')))"
# 应返回 "2" 之类;若超时/空/400 → vlm 链路坏(温度=1?坑#8)
```

### 8.3 #3134 回归验证(升级核心判据)
重跑 §6.2 的基线 query,与 `backups/search-baseline-v0.4.5-*.txt` 对比:
- **结果数 + top score 持平** → 未命中(或修复已合),可继续;
- **大幅下降** → 命中 #3134,**回退 v0.4.5**(§9),勿继续使用。

### 8.4 端到端入库(坑#11/#13)
按 `docs/E2E_TESTING.md` §8 走:建专用 `e2e`/`tester` account/user → memory 链路(remember→轮询→search/read,判据=召回 + abstract 非空)→ resource 链路(add_resource→轮询→search/read,判据=summary/overview 生成 + 召回 + read 完整)→ DELETE account `e2e` 级联清。
- **判据**:能召回 + abstract 非空合理;resource summary/overview 无 timeout;read 完整。
- 同时抽查 #3136:`.abstract.md` 是否是真实摘要(不是满屏 `# Working Memory` 标题)。

### 8.5 #3101 观察与索引完整性(坑#13 关联)
```bash
# 观察 down/重启时有无 SEGV / "Failed to flush/close index default" / "sys.meta_path is None"
docker compose logs openviking --since 10m 2>&1 | grep -iE 'segv|abrt|sys.meta_path|Failed to flush|Failed to close'
# 索引文件健康(坑#13:无 0 字节 manager_meta)
docker exec openviking find /app/workspace/vectordb/context/index/default/versions -name manager_meta.json -size 0
# 向量计数 + 实测 search 召回(坑#13)
HERMES_KEY="..."; curl -s "http://127.0.0.1:1933/api/v1/debug/vector/count" -H "X-API-Key: $HERMES_KEY"
```
若 down 时出现 SEGV / flush 失败 → #3101 命中;下次启动用 §8.5 第二条核查 index 没被写坏。

### 8.6 glob 语义(若升 v0.4.8+)
确认用到的 glob pattern 行为符合预期(`*.md` 当前目录、`**/*.md` 递归)。

---

## 9. 回滚

```bash
./scripts/update-openviking.sh v0.4.5     # 锁回 v0.4.5
# 验证同 §8.1–8.4(v0.4.5 基线)
# 向量数据无需处理:v0.4.9 写入的 vectordb 格式与 v0.4.5 兼容(§2.2),可直接读
```
> 仅当升级中 vectordb 被新版写坏时,才需从 §6.1 备份恢复(按坑#13 流程:恢复未损坏版本 + delta replay)。

---

## 10. 监控清单(等修复,定期复查)

- **[#3135](https://github.com/volcengine/OpenViking/pull/3135)**(#3134 修复 PR)—— 合并进正式版即可解除头号阻塞。
- **[#3101](https://github.com/volcengine/OpenViking/issues/3101)**(shutdown SEGV)—— 等修复 PR。
- **[#2989](https://github.com/volcengine/OpenViking/issues/2989) / [#3136](https://github.com/volcengine/OpenViking/issues/3136)**(检索质量回归)。
- **[#2118](https://github.com/volcengine/OpenViking/issues/2118) / [#1381](https://github.com/volcengine/OpenViking/issues/1381)**(local vectordb 返空长期坑)。
- 新版本 release notes:https://github.com/volcengine/OpenViking/releases

**触发升级的信号**:上述 #3135 + #3101 修复进入某正式版 + 发布满 1–2 周无新回归 → 启动 §6–§9。

---

## 附:决策一览

| 情境 | 动作 |
|---|---|
| **现在**(2026-07-11 核实后) | **暂留 v0.4.5**。做 §6.1 备份 + §6.2 基线(应急用)。不升级 |
| 想试用 v0.4.9 新特性(image search 等) | 在**另一套隔离环境**(独立 account/容器或专用测试机)验证,不在生产 hermes 数据上冒险 |
| #3135 + #3101 修复进正式版 + 稳 1–2 周 | 按 §6–§9 升级,可直接跳版本(无需补升中间版) |
| 升级后 §8.3 命中 #3134 / §8.5 命中 #3101 | 立即 §9 回退 v0.4.5 |
