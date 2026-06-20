# OpenViking 0.3 → 0.4 升级:变化与本项目适配

> **资料来源**:[v0.4.1 release notes](https://github.com/volcengine/OpenViking/releases/tag/v0.4.1)(对比 `v0.3.24...v0.4.1`)+ 本项目(v0.4.3)实地核查(2026-06)。
> **定位**:说明 0.3→0.4 在设计/实现/部署/使用各层的变化,**以及本仓库需要哪些适配**。
> 相关:`MULTI_USER.md`(User/Peer 模型)、`CLAUDE.md`(坑 + 架构)、官方[迁移指南](https://github.com/volcengine/OpenViking/blob/v0.4.1/docs/zh/migration/01-user-peer-model.md)。

---

## TL;DR

0.4 不是小版本迭代,是**数据模型升级**——Context 进入 **User/Peer 时代**。七条主线:User/Peer 身份模型、legacy 迁移、多模态入库、Recall Trace、Skills、模型 failover、存储 multi-write。

**本仓库(v0.4.3)适配结论**:

| 项 | 是否需要动 | 说明 |
|---|---|---|
| **ov.conf / compose / 脚本** | ❌ 无需强制改 | 0.4 schema 向后兼容;我们用的字段(server/storage/embedding/rerank/vlm/query_planner + temperature/max_tokens/timeout/text_source 等)0.4 全支持,v0.4.3 实测 |
| **legacy 数据** | ⚠️ **需要处理** | 本项目有 0.3.x 残留(`viking://agent/default`+`hermes`、多个 `viking://session/*`、测试 `user/e2e-test`)——见 §5 |
| **已适配(本轮会话)** | ✅ 已完成 | kimi vlm(`temperature:1`/`max_tokens`/`timeout`/`thinking`)+ embedding(`content_only`/`max_input_tokens`) |
| **可选增强** | 按需 | 多 user、credentials failover、storage 备份、多模态、skills(§6) |

---

## 1. 0.4 升级了什么(七主线)

### ① User / Peer 身份模型(最核心)
把"数据 owner"和"交互对象"拆开:`User`=数据归属,`Peer`=user 下的交互对象(客服客户/群聊成员/子 agent)。客服场景建模:`account=acme, user=support-bot, peer=customer-alice/bob`——平台只管 `support-bot` 的 key,每个客户用稳定 `peer_id` 隔离上下文。检索可用 `actor_peer_id` 切换 peer 视图(不改身份)。

### ② 0.3.x legacy 兼容 + 迁移
`viking://agent/...`、`viking://session/...` 升级后**仍可读**(兼容);提供 `ov --sudo admin migrate` 把旧数据复制到新 user/peer 模型(**向量也迁移:读旧 payload 重写 URI/owner,不重新 embedding**);确认无误后 `--cleanup` 删旧。

### ③ 多模态入库
`ParserRouter` 可把 pdf/docx/pptx/xlsx/mp4 路由到外部 Understanding API;图片消息(OpenAI `image_url`)经 VLM 转描述再入记忆;Markdown 图片引用改写为 `viking://`;PDF/DOC 抽图存 VikingFS;Feishu 用户 token / refresh token 导入。

### ④ 检索 + OpenClaw Recall Trace
检索 API 新增 `context_type`(限定 memory/resource/skill);OpenClaw 补 Recall Trace(召回可解释/可调试)、runtime query config、feature gates。

### ⑤ Skills 成 user-scoped 一等资产
`viking://user/{user}/skills/`,CLI/API 支持 add/list/find/update/delete/校验,可从 Git 安装。

### ⑥ 模型可靠性:ordered credentials + failover
VLM/Embedding 支持**多凭证数组**(顺序=优先级),区分错误类(429/5xx/超时→重试换凭证;认证/配额→换凭证;400/超大/内容安全→快速失败);Embedding 校验多凭证维度一致。

### ⑦ 存储可靠性:RAGFS multi-write
`storage.agfs.backups` 配 primary + 多 backup(S3 等),用于 HA/跨区副本/读加速/存储迁移。另:S3 content-type autodetect、向量迁移修正、`ov doctor` 现报无效 ov.conf 字段(不再误报 PASS)。

---

## 2. Breaking Changes(升级必读)

1. **`viking://agent/...` 仍可读,但不再是新写入目标**——新数据写 `viking://user/...`。
2. **`agent_id` 降级为 legacy 过渡**,映射到请求级 `actor_peer_id`;**不可同时配 `agent_id` 和 `actor_peer_id`**(服务端拒)。
3. legacy `agent_id` client **不要再显式传 message `peer_id`**。
4. **旧 `role_id` 记忆隔离废弃**——用 User/Peer 模型表达隔离边界。

---

## 3. legacy → user/peer 迁移机制

| 旧数据 | 新位置 |
|---|---|
| `viking://agent/<agent_id>/memories/...` | `viking://user/<user_id>/peers/<agent_id>/memories/...` |
| `viking://agent/<agent_id>/resources/...` | `viking://user/<user_id>/peers/<agent_id>/resources/...` |
| `viking://agent/<agent_id>/skills/<skill>/...` | `viking://user/<user_id>/skills/<skill>/...` |
| `viking://session/<session_id>/...` | `viking://user/<user_id>/sessions/<session_id>/...` |

迁移流程:备份 0.3.x → 升级 → 验证 `viking://agent`/`session` 可读 → `ov --sudo admin migrate --output json` → 检查 task/计数/新 URI → 业务写入切到 `viking://user` → 确认后 `migrate --cleanup`。

---

## 4. 各层变化速览

| 层 | 0.3.x | 0.4 |
|---|---|---|
| **设计** | agent_id 单一身份(数据 owner≈交互对象) | User(owner)+ Peer(交互对象)分离;account/user/peer 三层 |
| **实现** | `viking://agent/`、`viking://session/` 命名空间 | `viking://user/{user}/...`(含 peers/sessions/skills);legacy 兼容读 + migrate |
| **部署** | auth: api_key/root | 新增 **trusted**(网关注入 header)、**OAuth 2.1**(DCR+PKCE,Claude Desktop);RAGFS multi-write;parser_api | 
| **使用** | remember/search 按 agent | 按 user/peer;`actor_peer_id` 视图;`context_type` 检索;Skills;多模态消息 |

---

## 5. 本仓库适配分析(核心)

### 5.1 ov.conf / compose / 脚本 —— 无需强制改 ✅

0.4 schema **向后兼容**。本仓库 ov.conf 的所有段(server 的 auth_mode/root_api_key、storage 的 local agfs/vectordb、embedding/rerank/vlm/query_planner)及本轮新加的 `temperature`/`max_tokens`/`timeout`/`text_source`/`max_input_tokens`/`thinking`,在 v0.4.3 全部实测有效(`extra:forbid` 也没拒)。**不配 0.4 新段(parser_api/memory/credentials/backups 等)就用默认,不影响运行。**

> 0.4 没有废弃我们正在用的任何字段;`agent_id`/`role_id` 我们本来就没配。

### 5.2 legacy 数据 —— 需要处理 ⚠️(本项目实情)

实地核查发现本仓库 workspace 里有 0.3.x 残留:

```
viking://agent   → default, hermes              (0.3.x legacy agent)
viking://session → mcp-store-*, 20260604/0606_* (legacy/测试 session)
viking://user    → default, e2e-test            (e2e-test 是本轮测试残留)
```

这些**都是 0.3.x 时期 + 本轮测试的历史残留,不是真实业务数据**。三种处理(按推荐度):

1. **清理(推荐)**——本仓库是全新部署、无真实数据,legacy 是测试垃圾。直接删,不 migrate:
   ```bash
   set -a; source /mnt/hdd/tools/openviking/secrets.env; set +a
   # 用 root MCP forget 清 legacy agent/session + 测试 user 残留
   docker exec -i openviking python3 - << 'PYEOF'
   import os,httpx,json
   KEY=os.environ['OPENVIKING_ROOT_API_KEY']
   H={'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json, text/event-stream'}
   r=httpx.post('http://127.0.0.1:1933/mcp',headers=H,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
   H['Mcp-Session-Id']=r.headers.get('mcp-session-id')
   httpx.post('http://127.0.0.1:1933/mcp',headers=H,json={'jsonrpc':'2.0','method':'notifications/initialized'})
   for uri in ['viking://agent','viking://session','viking://user/e2e-test']:
       r2=httpx.post('http://127.0.0.1:1933/mcp',headers=H,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'forget','arguments':{'uri':uri,'recursive':True}}})
       print(uri,'→',[l for l in r2.text.splitlines() if l.startswith('data: ')][0][:90])
   PYEOF
   ```
2. **migrate(过度,本项目无真实数据不建议)**:`ov --sudo admin migrate --output json` 把 legacy 迁到 user/peer 模型。
3. **忽略**:0.4 兼容读 legacy,不处理也能跑;但 workspace 会一直留着旧命名空间数据。

> 本仓库当初 0.3.23→0.4.3 是直接换镜像、没跑 migrate——因为当时基本无数据。现在看到的 legacy 是那之后测试累计的残留。

### 5.3 已完成的适配(本轮会话)✅

针对 0.4 + kimi-for-coding reasoning 模型,已适配并验证:
- vlm/query_planner:`temperature:1`(kimi 只收 1)+ `max_tokens`(131072/65536,reasoning 留余量)+ `timeout`(1200/600,kimi reasoning 慢)+ `thinking:true`(语义);
- embedding:`text_source:content_only` + `max_input_tokens:6144`;
- 这些都是 v0.4.3 上实测通过(memory+resource 端到端)。

### 5.4 v0.4.4 的 role.value 阻塞 bug(与本仓库相关)

我们踩过的 [issue #2718](https://github.com/volcengine/OpenViking/issues/2718)(v0.4.4 PR#2709 把 `Role(str,Enum)`→`Role(str)` 致 `ctx.role.value` 崩,经 MCP 入库全挂)是 **0.4.x auth 重构的回归**——已降级 v0.4.3 规避,修复 PR#2728 在 main 未发版。**这正说明 0.4 的 auth/identity 大改是高风险区,升级必须端到端测**(见 E2E_TESTING.md)。

---

## 6. 可选增强(0.4 新能力,按需启用)

| 能力 | 何时启用 | 怎么配 |
|---|---|---|
| **多 user 隔离** | 接入 opencode/claude-code/hermes 等多 client | 见 `MULTI_USER.md`(方式 A: api_key+user_key;方式 B: trusted+官方插件) |
| **模型 failover** | 想提高 vlm/embedding 可用性(单点 kimi/qwen 抖动时) | vlm/embedding 段 `credentials:[{...},{...}]`(ordered,自动 failover) |
| **storage 备份** | 想给 workspace 加副本/备份 | `storage.agfs.backups`(multi-write,S3 等) |
| **多模态入库** | 要存 PDF/DOC/图片消息 | `parser_api`(外部解析)+ vlm 已支持图片(kimi supports_image_in) |
| **Skills** | 要把可复用能力做成 user-scoped 资产 | `ov skills add ...`(viking://user/{user}/skills/) |
| **OAuth 2.1** | 给 Claude Desktop 等强制 OAuth 的 client | ov.conf OAuth 段(DCR+PKCE,内置 provider) |

---

## 7. 建议(给本仓库)

1. **清掉 legacy 测试残留**(§5.2 方式 1)——workspace 目前混着 0.3.x agent/session + 测试 user,清理后干净,后续全用 user 模型。
2. **ov.conf 保持现状**——0.4 兼容,无需改;`agent_id`/`role_id` 不要配(已废弃)。
3. **接 client 前先定多 user 策略**(MULTI_USER.md):api_key 模式 + 每 client 一个 user key(方式 A),或切 trusted 用官方插件(方式 B)。
4. **继续手动版本锁定**(update-openviking.sh 参数 tag,不自动追 latest)——0.4.x 还在密集迭代且有回归(v0.4.4 bug),每版端到端测过再升。
5. **关注 v0.4.5+**:含 PR#2728(role.value 修复)的版本发布后,可考虑升;但仍按 E2E_TESTING.md §8 checklist 测。
