# kimi-for-coding(VLM / QueryPlanner 后端)特性档案

> **版本基线**:`openviking-server v0.4.4` + 实测 `api.kimi.com/coding/v1`(2026-06)。
> 定位:openviking 的 vlm/query_planner 后端 `kimi-for-coding` 的**完整行为档案**——它是什么、有什么脾气、openviking 怎么处理它、怎么配、怎么验。
> 配置陷阱浓缩在 `CLAUDE.md` 坑 #8/#9;本档是完整背景。

---

## TL;DR

- 当前底层是 **K2.7 Code High Speed**(256K context,强制 reasoning);
- **只接受 `temperature=1`**(实测 0/0.5/1.5/2 全拒);
- **强制 reasoning**,关不掉;`max_tokens` 预算被 `reasoning_content` 优先消耗;
- openviking **不认它为 reasoning 模型**(只认 gpt-5/o1/o3/o4),发的是 `temperature`+`max_tokens` 而非 `reasoning_effort`;
- 因此必须显式设 `temperature:1`、给 `max_tokens` 留余量;`thinking` 字段对它无效(语义上设 true)。

---

## 1. 它到底是什么模型

`GET /v1/models`(带 VLM api_key):

```json
{"data":[{"id":"kimi-for-coding","display_name":"K2.7 Code High Speed",
  "context_length":262144,"supports_reasoning":true,"supports_image_in":true,
  "supports_video_in":true,"supports_thinking_type":"only"}]}
```

- **底层 = K2.7(Kimi 2.7)Code High Speed**(kimi 单方面决定,用户不可选具体版本);
- 256K context;支持图片/视频输入;**`supports_thinking_type:"only"` = 强制 reasoning**。

### 如何确认底层模型(版本会随 kimi 更新)

- **唯一可靠途径**:`curl https://api.kimi.com/coding/v1/models`,看 `display_name`;
- chat 响应的 `model` 字段**永远回 `kimi-for-coding`**,不暴露 K2.7;
- openviking **不解析 `response.model`**(token 统计用的是本地配置的 model 名),所以日志里只有 `kimi-for-coding`。
- `k2p5`/`k2p6`/`k2p7` 不是用户可选的 model id;kimi 把 coding 线收敛成单一入口 `kimi-for-coding`。

---

## 2. 三个硬约束

### 2.1 只接受 `temperature=1`

穷举实测:

| temperature | 结果 |
|---|---|
| 0 / 0.3 / 0.5 | ❌ `invalid temperature: only 1 is allowed for this model` |
| **1** | ✅ 正常返回 |
| 1.5 / 2 | ❌ `temperature must not be greater than 1.000000` |

<1 和 >1 两层校验都收敛到唯一值 1。这是 reasoning 模型不允许调采样温度的硬约束。

### 2.2 强制 reasoning,关不掉

`supports_thinking_type:"only"` → 每次输出 = `reasoning_content`(先,思考)+ `content`(后,答案),**共享 `max_tokens` 预算且 reasoning 优先消耗**。openviking 的 `thinking` 字段对 kimi **完全无效**(只对 openai+DashScope 加 `enable_thinking`)。

### 2.3 max_tokens 被 reasoning 吃光 → content 空

reasoning 把预算用尽时,`content` 还没生成就被 `finish_reason=length` 截断 → `content=""`。openviking 只取 `content`、丢弃 `reasoning_content` → **静默拿到空结果**(不报错、healthy、doctor PASS)。

实测证据:`max_tokens=4` → `content=""`、`reasoning_content="The user said \""`、`completion_tokens=4`、`finish_reason=length`。

---

## 3. openviking 如何处理 kimi-for-coding

`KimiVLM`(继承 `OpenAIVLM`)→ 标准 OpenAI Chat Completions(`POST /chat/completions`)。关键处理(`kimi_vlm.py` / `openai_vlm.py`):

| 处理 | 行为 |
|---|---|
| api_base 规范化 | 不以 `/v1` 结尾则自动补 |
| model 别名 | `kimi-code`/`k2p5` → `kimi-for-coding`(历史遗留) |
| max_tokens 兜底 | 未设时 `DEFAULT_KIMI_MAX_TOKENS = 32768` |
| User-Agent | 自动加 `KimiCLI/1.30.0` |
| **是否 reasoning 模型** | `_REASONING_MODEL_PREFIXES = ("gpt-5","o1","o3","o4")` → **kimi-for-coding 不匹配,被当普通模型** |

因为被当普通模型,走 `_build_text_kwargs` 的 else 分支:

```python
if is_reasoning:                      # False for kimi
    kwargs["reasoning_effort"] = ...
else:
    kwargs["temperature"] = self.temperature   # ← 默认 0.0,被 kimi 拒
kwargs["max_completion_tokens" if is_reasoning else "max_tokens"] = max_tokens  # kimi 走 max_tokens
```

→ 发 `temperature=0.0` + `max_tokens`,kimi 因 temperature 400。**这就是坑 #8 的根因。**

---

## 4. 必要配置(本仓已设)

```jsonc
"vlm": / "query_planner": {
  "provider": "kimi",
  "api_base": "https://api.kimi.com/coding/v1",
  "api_key":  "${OPENVIKING_VLM_API_KEY}",
  "model":    "kimi-for-coding",
  "temperature": 1,          // 必须!默认 0.0 会被 kimi 拒
  "max_tokens": 131072,      // vlm;query_planner 65536。给 reasoning 留余量
  "thinking": true,          // 语义反映:模型确实在 reasoning(字段本身无效)
  "max_concurrent": 10
}
```

> `max_tokens` 是上限非固定消耗,设大日常成本/延迟不变,只抬高长 reasoning 最坏上限;`context_length=262144` 是 prompt+output 总预算的真正约束。

---

## 5. 验证方法(doctor 不够!)

`doctor` 的 `check_vlm` **只校验配置/api_key 存在,不发 chat**,永远报 PASS——**不能用它判 vlm 可用性**(坑 #8)。必须真实调用:

```bash
# 容器内用 openviking 自己的 vlm client 发真实 chat
docker exec -i openviking python3 -c "
import asyncio
from openviking_cli.utils.config import get_openviking_config as g
c=g()
print(asyncio.run(c.vlm.get_completion_async('1+1=?')))
"
# 或直接 curl(带 temperature=1)
curl -sS https://api.kimi.com/coding/v1/chat/completions \
  -H "Authorization: Bearer $OPENVIKING_VLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"kimi-for-coding","messages":[{"role":"user","content":"1+1=?"}],"temperature":1,"max_tokens":16}'
```

返回非空 content = 正常;返回 `invalid temperature` = temperature 配错;返回空 content + `finish_reason=length` = max_tokens 被 reasoning 吃光。

---

## 6. 模型版本变更的影响(kimi 换底层时)

kimi 可能随时把 `kimi-for-coding` 后端从 K2.7 换成更新模型。脆弱耦合点(按风险):

1. **temperature 约束**:现在只收 1;若新模型规则变,`temperature:1` 可能又触发 400(最该监控);
2. **reasoning 行为**:若更"爱思考",`max_tokens` 可能又被吃光 → content 空;
3. **字段兼容**:openviking 当普通模型发 `max_tokens`(非 `max_completion_tokens`);若 kimi 哪天像 OpenAI o 系 reject `max_tokens`,就会坏;
4. 响应格式 / 上下文长度 / 价格 / 限流可能变。

**监控**:升级/换模型后跑一次第 5 节的真实调用验证,别只看 doctor。
