# 归档仓(v0.4.5)结论对 v0.5.2.1 的对抗性核查与 embedding 截断标定

核查日期:2026-11-07
对象:`github.com/bytesnail/openviking`(2026-07 归档的 v0.4.5 本机部署配置仓,含 13 条"坑段"与 8 篇专题文档)中可能仍适用于本部署的结论;以及由此引出的 embedding 截断预算标定(§3)。
方式:容器内源码逐行核对(`/app/.venv/lib/python3.13/site-packages/`,v0.5.2.1,下文行号即此版本)、GitHub issue/release API、docs.openviking.ai、本机真实链路实测(适配器 18353 探针、服务端 planner 配置对象直连、服务端日志)。
凭据纪律:全程无凭据值出现;实测仅走本机无鉴权端点与服务端自身配置对象。

## 1. 五项候选结论判定

| 归档结论 | 判定 | 关键证据 |
|---|---|---|
| `max_input_tokens` 提 6144 先例 | 容量成立(经本栈标定,§3) | embeddinggemma-2 窗口 8192(serve.py ctx=8192→slot 8448;`/props` 实测 8448;官方 model card 8192);实测溢出风险方向与 #5895 相反(§3) |
| `text_source` 切 `summary_first` 绕截断 | 否决/搁置 | summary 路径确实不截断(embedding_utils.py:631-686),但全局换向量文本语义代价大,且无摘要卡回退正文仍截断;摘要覆盖率未验证 |
| sparse/hybrid 本地不可接入 | 成立(三源) | 注册表仅 volcengine/vikingdb(embedding_config.py:830/840/868/880);openai sparse/hybrid `NotImplementedError`(openai_embedders.py:445-486);官方文档明言 openai 兼容/ollama/local 均 dense-only |
| 坑 #13 索引损坏恢复 runbook | 需按 0.5.2.1 改写(§4) | 恢复不再依赖旧快照;`close()` 只留最新版本;"删 index 无效"与"reindex 不生成向量"均已失效 |
| 机制坑三条(down/up、expandvars、reasoning 预算) | 两条成立、一条修正(§2) | expandvars 静默残留成立(config_loader.py:87 注释原文);"restart 无效"不成立(§2);reasoning 吃预算在 GLM 栈有本机日志实证 |

## 2. 对旧归档具体说法的修正(含"已被超越"复核)

- **"改 ov.conf 后 restart 无效,必须 down/up"——不实**。2026-11-07 22:25 实测:同容器 stop/start(容器日志缓冲连续,证明未重建),重启后容器内挂载文件 md5 与宿主一致,服务端加载值即新值(65536/disabled/10s 逐项核对)。单文件 bind 的真实坑是**编辑后不重启则容器沿用旧 inode**;`docker compose restart openviking`(README 现规程)足够。
- **坑 #12(entrypoint 覆盖)在本部署已正确处理**:entrypoint 的 HOST 只读 env、PORT 优先级 env→ov.conf→1933;compose.yaml:57-58 已显式设置。
- **"#3135 是修复"——不实**:PR closed(2026-10-15)但未合并;#3134(find 丢 L2 命中回归)实际由 #4472(2026-08-31 merged)关闭,至少含于 v0.5.0。**#3101(StreamableHTTP shutdown SEGV / Failed to flush index)至今 open**,是写快照中断的现存来源。
- **"#2709 是修复"——反了**:它(auth 插件化重构)是 role.value 回归的引入方,修复是 #2728(2026-06-19 merged,进版版本号未能确认)。
- **KimiVLM 32768 兜底与 `_is_reasoning_model` 均已消失**:v0.5.2.1 按模型名正则只认 OpenAI o/gpt-5 系(改发 `max_completion_tokens`);`max_tokens` 未配置则**不发该参数**(base.py:76、openai_vlm.py:195-197)。
- **"rerank 复用 embedding 截断文本"——过时**:0.5.2.1 rerank 独立截断(query 占 `max_input_tokens*3/4`,documents 为候选 abstract 标量,hierarchical_retriever.py:255/320-332);RerankConfig 新增 timeout(默认 30.0)/max_input_tokens(默认 0=不截断)/log_payloads,仍无 max_concurrent。
- **"summary_first≡summary_only 同一集合"——现为显式 deprecated 别名改写**(embedding_config.py:709-713,warning 明言无摘要仍回退 content);摘要生成与 text_source 无关、计划驱动(semantic_executor.py:218-236)。
- **"改 embedding 模型必须重建"——细化为仅维度变化必须重建**:同维度换模型/端点可配 `embedding.allow_metadata_override` 保留已索引数据(改写 collection 元数据并告警,embedding_config.py:672-681);维度存于 `collection_meta.json` 并严格校验。

## 3. embedding 截断标定(B1 阶段 1,2026-11-07 实测)

估算器(v0.5.2.1 `openviking/utils/embedding_input.py`):quarter-token 单位,CJK 1 字=1 token、ASCII 4 字符=1 token;本地复刻与容器实现逐字节一致(prefix 19195=19195)。经适配器 18353(生产路径)实测 `usage.prompt_tokens`(真实 embeddinggemma-2 分词):

| 输入 | 估算 token | 实际 prompt_tokens | 因子 |
|---|---|---|---|
| openviking.md 整卡(22336B,混合中文 markdown) | 5736 | 6382 | 1.113 |
| 同卡截断 @4096 | 4103 | 4378 | 1.069 |
| 同卡截断 @5120 | 5127 | 5555 | 1.085 |
| 纯中文合成 @4096 | 4097 | 3144 | **0.768(高估=保守)** |
| 纯中文合成 @6144 | 6145 | 4714 | 0.767 |

- **#5895 的 CJK 1.4–1.7× 低估因子不可迁移到本栈**:embeddinggemma-2 中文 ~1.3 字/token,估算器在纯中文上高估(安全方向)。
- 4096 现状的代价:meteor 名下 **5 张卡**(review_fixes_applied 5790、openviking 5736、归档核查 5627、review_findings_reported 5238、invariant_audit_report 5236)各 **22–29% 内容向量不可见**;全库估算最大 5790。
- **内容类别决定安全边界(2026-11-08 补测收窄)**:硬边界为服务端 slot_ctx **8448**(实测 CJK 实际 8437 通过、≥8979 失败;超限为适配器 **502 "embedding upstream failed"**,即时可见、非静默截断;model card 的 8192 是质量窗口,服务端执行的是 8448)。实测因子分档(由低到高):英文散文 0.59、纯中文 0.77、**记忆卡(5 张大卡全实测)1.02–1.17**、真实 Python/bash 1.03–1.22、紧凑配置(YAML)~1.39、符号密集合成样式 1.83(病态悲观端,est 4608 刀锋/4800 起败)、**minified JSON ~2.08(est 4096 即败→现行 4096 预算下该失败类已存在)、base64 ≳2.75(est 3072 即败)**。
- **记忆卡语料核查(2026-11-08)**:126 张 .md 中仅 15 张含 fenced 代码块(12%),全库最大单块 est 2234 token(占其卡 30%,系 review 修复摘录而非整文件);含块最多的 review 系列卡因子反而最低(1.02)——代码摘录被中文叙述稀释。**"记忆卡整段引用代码"在真实语料中不成立**,病态密集样式只会经"导入机器格式资源"路径进入。
- **结论(2026-11-08 二次修正后)**:**"6144 覆盖现存全部卡"成立且为生产函数直证**(5 张大卡 `truncate(·,6144)` 原样返回 True;整卡实际 token 5925–6570,全距 8448 内)。因子按子类:真实代码/脚本(1.03–1.22)在 6144 下截断后 7027–7506 安全;紧凑配置(YAML ~1.39)安全上限 est≈6078,预算 6144 顶部刀锋越界。**修正一处早前说法**:机器密集格式(minified JSON ~2.08、base64 ≳2.75)在**现行 4096 预算下就已失败**(4096×2.08≈8520>8448;3072×2.75≈8448),并非提高预算所引入——4096→6144 真正新增的失败面只有"YAML 类 est>6078"这一个角落;机器格式的治理手段是导入前注意/拆分(失败可见 502),压预算到 3072 以下才能挡 base64 且代价远大于收益。session 命名空间跳过索引(embedding_utils.py:776-777)。规划锚点:**1.17(真实卡上界)⇒ 6144 安全;1.39(真实文件上界)⇒ ~6000 且仍覆盖全部卡;1.5 不是好的规划点(⇒预算 ≤5632,既盖不住最大卡 est 5790,也挡不住 ≥1.83 的机器格式)**。
- 阶段 2(待批,未执行)选项随之更新:**6144**(现存卡全可见;接受"code 密集文本资源 est>~4600 向量化 502"这一可见失败类)/ **~4400**(任意内容安全,但 5 张大卡尾部仍部分不可见)/ **维持 4096 + 拆卡**。若执行:ov.conf `embedding.max_input_tokens` → 值 → `docker compose restart openviking` → `content reindex mode=vectors_only`(对已入库文件按当前配置重算向量,reindex_executor.py:1589;skill 类即使 md5 未变也强制重建)→ deployment-smoke 冒烟 + 大卡尾部内容召回对照。

## 4. vectordb 恢复要点(0.5.2.1 源码核实;操作化版本见 README"备份与恢复")

布局实测:`context/index/default/versions/<纳秒ts>/{manager_meta.json,scalar_index,vector_index}` + `<ts>.write_done`;启动只加载最新版本(local_index.py:1453-1467)。与旧版关键差异:

1. `_recover()` 从 LevelDB store(`context/store/`)全量重建并重放 delta(local_collection.py:1322/1369-1432;日志 `Index 'default': replaying delta records lazily...`/`replay complete`)——**不再需要未损坏旧快照**,仅需保留 `index/default/index_meta.json`(其缺失才跳过 recovery);
2. `close()` 清理旧版本只留最新(local_index.py:1527-1541)——**多版本快照不是安全网,backups/ 才是**;
3. `vectors_only` reindex 真重建向量(reindex_executor.py:774-778)——旧"删 index 重建 + reindex 不生成向量"路径已失效;
4. 0 字节 `manager_meta.json` 崩溃路径:Python 侧无捕获,引擎异常直接传播 crash loop(未在本机复现;#3101 仍是中断源)。

## 5. z.ai / glm-5.3-flash 实测(生产路径)

- **coding 端点接受 `thinking.type=disabled`**:2026-11-07 两次独立实测(直连终测 5 查询;23:1x 服务端 planner 配置对象直连 5.3s 成功返回),与 z.ai 文档页"GLM-5.3 系 disabled 报错"矛盾,以实测为准;thinking 是否真被服务端关闭无法从外侧证实(延迟谱 3.7–5.6s 与终测一致)。`GLMVLM` 继承 `OpenAIVLM`,`extra_request_body` 透传在 glm 路径有效(openai_vlm.py:201-205)。
- **thinking 与 content 共享输出预算(本机日志实证)**:`output cap=32768, completion_tokens=32768, response_preview=''` 的 ERROR 形态即推理吃光预算、content 为空;vlm 段无手段真关思考,靠 `vlm.max_tokens`(2026-11-07 22:25 起 65536)容纳。65536 的验收判据:会话结束的 Phase 2 提取不再出现 `output cap=` 截断 ERROR。
- **query_planner 整段替换**:该段有配置即不逐字段继承 vlm(open_viking_config.py:702-706),未配字段走自身默认 timeout 600s / max_concurrent 32;`max_tokens` 未配不发参数(z.ai 端默认 65536)。
- **expansion 成功时日志静默**:状态只在检索响应体 `query_expansion` 字段(pipeline.py:201),失败才 WARNING(expansion.py:66);expansion 仅在带 `session_id` 且会话有内容时尝试(pipeline.py:70-96)。

## 6. 上游锚点(2026-11-07 时点,防止重复调查)

- open:#3101(关机 flush/close 失败)、#5895/#5760/#6418(截断估算类;因子与本文栈实测不符)、#6109(未合并,sparse 配置面收敛方向)、#7162(hybrid 索引写入流损坏)。
- sparse 本地仍 dense-only;0.5.x release notes 无 sparse provider/reindex/env 展开/glm provider 条目。
