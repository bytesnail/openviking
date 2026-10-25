# Z.AI 官方文档论断核查(2026-10-25)

## 核查方式与日期

- **核查日期**:2026-10-25。
- **方法**:使用 WebFetch 直接抓取 Z.AI 官方文档站 `docs.z.ai` 的 6 个一手页面(见下),只采用官方一手来源,未使用任何二手转述。引文为页面原文(英文),按页面小节定位。三态结论:✅确认 / ❌不符 / ⚠️无法验证。本报告不含任何凭据。
- **抓取页面**:
  1. https://docs.z.ai/devpack/quick-start
  2. https://docs.z.ai/devpack/latest-model
  3. https://docs.z.ai/guides/vlm/glm-5.3-flash
  4. https://docs.z.ai/api-reference/llm/chat-completion
  5. https://docs.z.ai/devpack/tool/others
  6. https://docs.z.ai/devpack/usage-policy
- 全部 6 页均成功取回,无「无法验证」项。

## 核查结果总表

| # | 论断 | 结论 | 证据 URL | 备注 |
|---|------|------|----------|------|
| 1 | Coding Plan 的 OpenAI Chat Completions base URL 为 `https://api.z.ai/api/coding/paas/v4`;普通 `https://api.z.ai/api/paas/v4` 不能替代 Coding Plan 场景 | ✅ | quick-start(Base URL 配置表)、api-reference/llm/chat-completion(Server 定义)、devpack/tool/others(endpoint 警告) | 文档无单句直陈「paas/v4 不可替代」,结论由三处表述综合支撑,详见下文 |
| 2 | `glm-5.3-flash` 对通用 OpenAI 兼容接入有效,且支持文本与图像输入 | ✅ | api-reference/llm/chat-completion(`ChatCompletionVisionRequest` model 枚举)、guides/vlm/glm-5.3-flash(Model Code、输入模态)、devpack/latest-model(多模态说明) | 出现在通用 `/paas/v4` Chat Completions 的视觉请求模型枚举中;输入模态 Video / Image / Text / File |
| 3 | GLM-5.3-Flash 模型页列出 1M context 与 128K max output | ✅ | guides/vlm/glm-5.3-flash | 页面标签原文:"Context Length: 1M"、"Maximum Output Tokens: 128K" |
| 4 | `[1m]` 后缀只出现在 Claude Code 1M 上下文配置说明中,需配合 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`;通用 Chat Completions 枚举无此后缀 | ✅ | devpack/latest-model(Claude Code 小节)、api-reference/llm/chat-completion(model 枚举) | 「只出现在」的核查范围为本次抓取的 4 个模型/接入页面,均未见其他出现位置 |
| 5 | 条款把使用范围限定为官方支持的特定工具/产品;官方支持列表未列 "OpenViking" | ✅ | devpack/usage-policy、devpack/tool/others | 与预期一致:OpenViking 未列出(页面上相近名称为 OpenCode / OpenClaw) |
| 6 | 条款/文档中是否有明确允许或禁止第三方工具调用 Coding Plan endpoint 的表述 | ✅(存在禁止性原句,带限定) | devpack/tool/others、devpack/usage-policy | 禁止通过「限定官方列表 + 范围外不得使用权益」实现;无任何逐字提及 "third-party"、RAG、memory 类服务的明文,详见下文 |

## 各论断详细证据

### 论断 1 — Coding Plan base URL ✅

- **quick-start 页**(Base URL 配置表):原文 "GLM Coding Plan supports both the Anthropic and OpenAI protocols. Make sure to configure the correct `Base URL`"。其中 **OpenAI Chat Completions 协议行为 `https://api.z.ai/api/coding/paas/v4`**(另有 Anthropic Messages:`https://api.z.ai/api/anthropic`;OpenAI Responses:`https://api.z.ai/api/v1`)。
- **通用 endpoint 与 coding endpoint 确为两个地址**:API 参考页 chat-completion 定义的 server 为 `https://api.z.ai/api`、路径 `POST /paas/v4/chat/completions`,即通用 OpenAI 兼容地址为 `https://api.z.ai/api/paas/v4`;该页未提及任何 coding 变体。
- **不可替代性**:
  - tool/others 页:"Incorrect endpoint configuration will result in inability to use GLM Coding Plan subscription quota."
  - usage-policy 页:"GLM Coding Plan may only be used within officially supported tools and products."
  - quick-start 页并注明:"API Keys for Individual Plans, Team Plans, and the standard model API are now managed separately."(Coding Plan 与标准 API 的 key 分开管理,佐证二者是不同体系。)
- **限定说明**:文档没有一句话直接写「`/api/paas/v4` 不能用于 Coding Plan 场景」;「不能替代」这一结论由「必须配置正确 Base URL」+「错误 endpoint 配置将无法使用订阅额度」+「权益仅限官方支持工具」三处表述综合得出。

### 论断 2 — `glm-5.3-flash` 通用 OpenAI 兼容有效 + 多模态 ✅

- **chat-completion API 参考页**:视觉请求 schema `ChatCompletionVisionRequest` 的 `model` 枚举为 `glm-5.3-flashx`(默认)与 **`glm-5.3-flash`**;消息内容项 `VisionMultimodalContentItem` 支持 `image_url`(页面注明 "Image URL or Base64 encoding. Image size limit is under 5M per image")。页面称 GLM-5.3-Flash 系列 "supports visual understanding, delivering excellent multimodal comprehension and tool calling"。
- **模型页** glm-5.3-flash:Model Code 列出 `glm-5.3-flash`、`glm-5.3-flashx`、`glm-5.3-flashx-legal`;输入模态 "Video / Image / Text / File",输出 "Text"。
- **latest-model 页**:"GLM-5.3-FLASH is a multimodal model, so **Support Images** can be checked"(对比:"GLM-5.3 is a text-only model")。
- 即:`glm-5.3-flash` 出现在通用 `/paas/v4` Chat Completions 官方模型枚举(视觉请求)中,文本与图像输入均支持。

### 论断 3 — 1M context / 128K max output ✅

模型页(https://docs.z.ai/guides/vlm/glm-5.3-flash)原文标签:

- "Context Length": **1M**(参数设置节另述 "support for a 1M-token context window")。
- "Maximum Output Tokens": **128K**。

### 论断 4 — `[1m]` 后缀仅见于 Claude Code 配置 ✅

- **latest-model 页,Claude Code 小节**:"To enable GLM 1M context, add the `[1m]` suffix to the model name (e.g., `glm-5.3-flash[1m]`)";同一 `settings.json` 的 `env` 块含 `"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000"`,文档指示在启用 1M 后缀的同时配置该压缩窗口参数;`/status` 检查处写明 Model 应显示 "glm-5.3-flash or glm-5.3-flash[1m]"。
- **同页 OpenAI 兼容示例**(Cline)只写 "enter the model name, such as `glm-5.3` or `glm-5.3-flash`",无后缀形式。
- **chat-completion API 参考页**:文本枚举(glm-5.3 / glm-5.2 / glm-5.1 / glm-5 / glm-4.7 / glm-4.6)与视觉枚举(glm-5.3-flashx / glm-5.3-flash)均无 `[1m]` 后缀。
- **模型页**的三个 Model Code 亦无后缀形式。
- 限定:「只出现在 Claude Code 说明里」系对本次抓取的 4 个模型/接入相关页面的核查结论,未发现其他出现位置。

### 论断 5 — 使用范围限定 + OpenViking 未列出 ✅

- **usage-policy 页**:"GLM Coding Plan may only be used within officially supported tools and products.";要点标签 "Use limited to supported tools";"Use in unsupported tools may result in restricted benefits."
- **tool/others 页**:"The GLM Coding Plan is limited to use within the following officially supported tools and product environments",随后列出:
  - 编码代理工具(16):ZCode(1.5× Usage)、Claude Code、Claude for IDE、Codex、OpenCode、Pi、Cursor、Cline、TRAE、Qoder、Droid、Kilo Code、Roo Code、Crush、Goose、Eigent;
  - 通用代理工具(4):AutoClaw(1.5× Usage)、OpenClaw、Hermes Agent、SillyTavern。
  - 并注明:"users may not use their subscription benefits for tools or scenarios outside of this scope."
- **"OpenViking"**:quick-start、tool/others、usage-policy 三页均未出现该名称(页面上相近名称为 OpenCode / OpenClaw,系不同产品)。**与预期一致:官方支持工具列表未列出 OpenViking。**

### 论断 6 — 对第三方工具的允许/禁止表述 ✅(存在禁止性原句,带限定)

存在与「非列表内工具」直接相关的限制性原句(英文原文):

1. tool/others 页:"**users may not use their subscription benefits for tools or scenarios outside of this scope.**"(范围外工具/场景不得使用订阅权益——最接近「禁止」的明文。)
2. usage-policy 页:"**Use in unsupported tools may result in restricted benefits.**"(后果式表述。)
3. usage-policy 页:"**GLM Coding Plan may only be used within officially supported tools and products.**"
4. 另有相邻的反共享条款(非工具范围问题):"Account sharing or multi-user access is prohibited."

**限定说明**:全部条款中**没有**任何一处逐字出现 "third-party"、RAG、memory、proxy、resale 等字样;也没有「明确允许」列表外工具的表述。禁止是通过「限定官方支持工具列表 + 范围外不得使用订阅权益 + 不支持工具可能受限」这一组合实现的。对「非官方列表内的 RAG/记忆类服务调用 Coding Plan endpoint」这一具体情形,可援引上述第 1–3 句作为限制依据,但文档未对该类服务单独点名。

## ❌ 与 ⚠️ 汇总

- **❌ 不符:无。**
- **⚠️ 无法验证:无。**(6 个指定页面全部成功抓取;6 条论断全部 ✅,其中第 1、6 条为带限定说明的确认,限定内容见对应小节。)
