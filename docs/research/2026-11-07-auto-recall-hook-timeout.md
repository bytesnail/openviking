# Claude Code UserPromptSubmit hook 超时报错:auto-recall.mjs timed out after 60s

- **初版**:2026-11-07(客户端侧只读排查)
- **修订**:2026-11-07 晚——完整根因闭环、修复已落地并验收(本版全面取代初版结论)
- **现象**:多个 Claude Code 会话反复出现
  `UserPromptSubmit hook [node ${CLAUDE_PLUGIN_ROOT}/scripts/auto-recall.mjs] timed out after 60s — output discarded.`
- **状态**:根因闭环;本仓 `config/ov.conf` 与宿主 `~/.openviking/ovcli.conf` 已按 §6 修复并验收;遗留项见 §9。

## 一句话结论

超时主因是**宿主侧召回压缩**:插件在 Claude Code 上默认 `recallCompress="auto"`(schema `harness` 覆盖,非用户配置),对每条 ≥1500 字符的召回 context(本机常态 2500–4000 字符)spawn `claude -p --model haiku` 做压缩,而 haiku 经宿主 settings.json 映射为 **glm-5.3-flash[1m] @ bigmodel**,真实记忆文本实测压缩耗时 **32–97s**,必然周期性击穿 60s hook 预算,记忆注入整块丢弃。同源伴生问题:query expansion 5s 熔断 vs 思考模型 17–34s(29/29 全败);独立问题:Phase 2 记忆提取被 32768 token 输出地板截断(3 次,记忆数据丢失)。修复:压缩迁移到服务端 query_planner(关思考,实测 11–17s)+ `vlm.max_tokens` 65536。

## 1. 链路与角色

```
用户发消息 → CC UserPromptSubmit(60s 同步预算)
  → auto-recall.mjs(宿主)→ OpenViking 容器 :1933
      ├─ 检索+组装 context face      实测恒 ~0.2s ✅
      ├─ query expansion(GLM,5s 熔断) 实测 17–34s ❌ 全部超时回退(修复前)
      └─ rewrite/digest(30s 熔断)     默认未启用(修复后启用)
  → 宿主压缩(≥1500 字符)spawn claude CLI   实测 32–97s ❌ 修复前主因
```

两个 LLM 环节是**同一模型 glm-5.3-flash 的两条调用路径**:服务器 z.ai 直连 / claude CLI→bigmodel anthropic 兼容层(后者慢 4–6 倍)。

## 2. 证据链(诊断实录)

| # | 证据 | 数据 |
|---|---|---|
| 容器资源 | `docker stats` | CPU 2%、480MiB——排除资源挤压 |
| 召回延迟 | `last-recall.json` 多样本 | 13.8s(no_results)↔ 56.7s(ok)↔ 手动复现 78s——常态贴 60s 线 |
| 服务器检索 | `ov find` ×3 + DEBUG 分解 | 恒 ~200ms;慢全部在 `recall_local_compression`(23.6/72/78s) |
| **真实文本基准①(客户端)** | 98 张真实卡,2k/6k/12k/18k ×2,逐字复刻压缩模板 | **32–97s,全部 >35s**;6k 档最慢 96s;触发档位全部超 60s |
| **真实文本基准②(服务端)** | z.ai 直连+`thinking:disabled`+官方 `recall_rewrite` 模板 | **6k:11.3/12.1s;18k:15.6/16.8s**;思考开对照 49.1s(超 30s 熔断) |
| expansion | 服务器日志+同形态模拟 | 24h 29 次失败,冒号后空消息=TimeoutError;思考开 17/25/34s(reasoning 815),关后 2–6s |
| 提取截断 | 服务器日志 | 3 次 `finish_reason='length', output cap=32768`;来源 `_DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS=32768`(extract_loop.py:58) |
| 配置事实 | 生效配置实测打印 | `recallCompressTimeoutMs=110000`(=recallTimeoutMs 120000−10000 推导,plugin-config.mjs:264-266);CC 默认 `recallCompress="auto"`(config-schema.mjs:115 harness 覆盖);阈值 `recallCompressMinInputChars=1500` 字符 |
| 关键结构 | 源码 | 服务端 rewrite 用 **query_planner 角色**(pipeline.py:157-176);`recallRewrite="server"` 时客户端压缩器不创建(host-compressor.mjs:109) |

## 3. 问题清单与因果链

- **P1(主因)召回压缩超时**:CC 默认 auto → 每条消息 spawn claude CLI → bigmodel 链路 32–97s → 超 60s → SIGKILL+记忆丢弃。压缩超时推导值 110s 远超 hook 预算,任何超时设置都救不了同步链路(触发档位全部 >60s)。
- **P2 expansion 全败**:`recall_intent_timeout_s=5.0` vs glm-5.3-flash 思考延迟 17–34s——量级错配,fail-closed 回退。功能无损但每条消息白等 5s,扩展查询增益从未兑现。
- **P3 提取截断(数据丢失)**:ov.conf vlm 段无 `max_tokens` → 32768 地板 → 整次会话记忆提取失败。模型实际支持 128K 输出。
- **P4(次生,未处理)大卡检索可见性**:无单卡大小上限配置;`embedding.max_input_tokens=4096` 硬截断向量化输入,本机 top 卡 15–21KB 可能已越线,深层内容向量检索不可达。

## 4. 被推翻的中间结论(方法论记录)

1. **初版报告结论**"管线请求多、服务器负载抖动"不成立:服务器检索 200ms,主因在宿主压缩器;
2. "压缩默认 30s 超时没拦住 70s+,疑 bug"——实为 110s 推导值,机制无 bug(8s 强制预算实测真实拦截);
3. "输出吞吐下限 ~60s,关思考救不了压缩"——被服务端基准推翻(z.ai 直连 11–17s);
4. **低信息密度测试文本低估真实压缩耗时 2–5 倍**(15–70s vs 32–97s)——性能基准必须用真实生产文本与逐字复刻的真实模板;
5. 模拟 planner 输出用字符串数组会被 `intent_analyzer.py:114` **静默丢弃**(要求对象数组 `{query, context_type, priority}`)——协议验证必须用官方模板;
6. `vlm.max_tokens` 是"单次响应总输出预算"(分析+全部记忆操作代码),**不是记忆卡上限**;因果方向是卡大小→输出需求。卡无直接上限配置,三层实际约束:vlm.max_tokens(间接整包)/ embedding.max_input_tokens(检索可见性)/ maintenance_review_tokens=1000(软提醒)。

## 5. 修复方案与执行记录(2026-11-07 晚落地)

### 本仓 `config/ov.conf`(重启生效,已执行)

```jsonc
"vlm":      { ...现有..., "max_tokens": 65536 },   // 治 P3:2× 观测需求,保留膨胀警报;模型上限 128K
"query_planner": {                                     // 治 P2 + 承接服务端压缩(expansion 与 rewrite 共用此角色)
  "provider": "glm", "model": "glm-5.3-flash",
  "api_base": "https://api.z.ai/api/coding/paas/v4",
  "api_key": "${Z_AI_API_KEY}",
  "extra_request_body": { "thinking": { "type": "disabled" } }
},
"retrieval": { "recall_intent_timeout_s": 10 }         // 5→10:容纳关思考后 2–6s,10s 兜底
```

**注意**:`recall_rewrite_timeout_s`(服务端压缩熔断)采用**默认值 30.0s,显式不写、不写即生效**——对实测 11–17s 有近 2 倍余量;其调整空间受 45s 客户端 deadline 与 60s hook 夹逼,若实测逼近 30s 应回基准查因而非调大。

### 宿主 `~/.openviking/ovcli.conf` plugin 段(已执行,打码脚本改写、权限 600 保持)

```jsonc
"plugin": { "recallRewrite": "server" }   // 治 P1:客户端压缩器不创建;body.rewrite=true → 服务端 digest
```

**不需要**(失去作用对象):`recallCompress: off`、阈值调整、压缩超时调整。

### 预算链验算

```
expansion ≤10s 熔断(实测 2–6s)+ 检索 ~0.2s + rewrite ≤30s 熔断(实测 11–17s)
→ 最坏 ~40s < 45s 客户端 deadline(SERVER_REWRITE_REQUEST_TIMEOUT_MS) < 60s hook——全链路无超时点
```

## 6. 验收记录

| 项 | 方法 | 结果 |
|---|---|---|
| 服务健康 | restart + `/health` `/ready` | 全绿;pydantic 校验通过(键名错误会启动失败) |
| CC 端到端(人造 session) | `OPENVIKING_DEBUG=1` 跑 auto-recall | **wall 11s**(修复前 78s);`rewrite:"ok"`(此前 "off");**无 `recall_local_compression` 阶段**(客户端压缩器确认未创建);context 1419 tokens 压至 455 |
| CC 端到端(真实会话 session f1d362d9) | 同上 | wall 9s,rewrite ok |
| expansion 对照 | 服务器日志 | **同一 session:22:24:35 旧配置 failed(5s 熔断)→ 修复后无任何 failed**;成功路径的 IntentAnalyzer INFO 不在容器日志级别内,以上述同 session 对照为准 |
| OpenCode(package 2026.11.5-1) | npm 拉包静态验证 | 共享库与 CC 0.7.21 同源;`config.mjs` 注释确认读 ovcli.conf 顶层 `plugin` 段(`plugin.opencode` 可覆盖);**无客户端压缩器**;`recallRewrite:"server"` 对其同样触发服务端压缩 |
| Phase 2 提取 | 观察项 | 修复后日志不应再出现 length 截断(需后续会话积累验证) |

## 7. 作用域:对 OpenCode 与未来 agent 工具

- **服务器侧三项(max_tokens / query_planner / intent_timeout)无条件全局生效**:任何 harness 提交的会话提取、任何客户端的 context 请求一律受益;
- **`plugin` 顶层段按共享设计对全部 harness 生效**(含 OpenCode;`plugin.<harness>` 可单独覆盖);
- schema 的 `harness:{claude_code:"auto", codex:"auto"}` 表明 **Codex 插件默认同样开启客户端压缩**——plugin 顶层段显式 `recallRewrite:"server"` 优先级高于 harness 默认,一并钉住;
- OpenCode 运行时行为待日常使用时观察(静态已确认机制);版本升级须按既有纪律逐实例重新验收。

## 8. 遗留与后续

| 项 | 说明 | 优先级 |
|---|---|---|
| 上游 issue | 素材完整:CC 默认 auto + 110s 推导超时 vs 60s hook;真实文本基准 32–97s;`intent_analyzer.py:114` 静默丢弃字符串元素 | 可代拟 |
| 大卡拆分(P4) | top 卡 15–21KB 超 embedding 4096 token 可见性线;手段:人工拆卡 / `maintenance_review_tokens` 提醒 / `custom_templates_dir` 定制提取模板 | 观察 |
| Phase 2 验证 | 等待后续会话的提取成功样本确认 65536 足够 | 观察 |
| flashx 实验 | `recallCompressModel` 指向更快模型的探索路径,服务端方案落地后无必要 | 低 |

## 9. 关键文件与参数索引

| 文件/参数 | 作用 |
|---|---|
| `~/.claude/plugins/cache/openviking/openviking-memory/0.7.21/scripts/auto-recall.mjs` | UserPromptSubmit 钩子入口;`OPENVIKING_DEBUG=1` 出分步日志(`~/.openviking/logs/cc-hooks.log`) |
| 同上 `shared/recall-core.mjs` / `recall-compress-core.mjs` | 召回主流程 / 客户端压缩(1500 字符阈值、digest 缓存) |
| 同上 `lib/host-compressor.mjs` | spawn claude CLI 压缩器(`recallRewrite=server` 时不创建) |
| 同上 `shared/plugin-config.mjs:264-266` | 压缩超时 110s 推导链 |
| `config/ov.conf`(本仓) | vlm.max_tokens / query_planner / retrieval 段 |
| 容器 `openviking/retrieve/context_assembler/{expansion,rewrite,pipeline}.py` | expansion 5s 熔断(fail-closed)/ 服务端 rewrite(30s 熔断,query_planner 角色) |
| 容器 `openviking/session/memory/extract_loop.py:58` | 提取输出 32768 地板(vlm.max_tokens 覆盖) |
| 容器 `openviking_cli/utils/config/retrieval_config.py` | `recall_intent_timeout_s=5.0` / `recall_rewrite_timeout_s=30.0` / `enable_intent` |
| `~/.openviking/ovcli.conf` plugin 段 | 跨 harness 插件行为配置(本文 `recallRewrite:"server"`) |
| `~/.openviking/state/last-recall.json` | 每次召回延迟/结果快照(超时被杀的运行不落盘) |
