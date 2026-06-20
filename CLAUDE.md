# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 仓库性质

这不是源码项目,而是 **openviking**(volcengine OpenViking,上下文数据库 / MCP server,docker 镜像 `openviking/openviking:latest`)的**本机部署配置仓**。仓库只含 `docker-compose.yml` + `ov.conf` + 密钥管理文件;openviking 自身运行在容器内,源码在镜像里(`/app/.venv/lib/python3.13/site-packages/openviking_cli/` 与 `openviking`)。

部署形态:embedding/rerank 后端为本机 **qwen3_embed**(llama.cpp `llama-server`),vlm/query_planner 用远程 **kimi** API。

## Git 工作流

个人仓,简单工作流:**直接在 `main` 分支提交,不使用 feature branch / PR**。改动落地后直接 `git add -A && git commit -m "..."` 落到 main,无需走评审流程。`secrets.env` 与 `workspace/` 已在 `.gitignore` 中忽略,不会被提交——真实密钥永远只活在 `secrets.env`(本机)。

## 架构与网络(理解一切配置的前提)

- openviking 容器 `network_mode: host` → **容器内 `127.0.0.1` 就是宿主机**。所有"本地后端"地址都写 `127.0.0.1`。容器对外监听 `0.0.0.0:1933`。
- **对外暴露注意**:host 网络下容器直接监听宿主**所有网卡**的 `1933`(ov.conf `server.host:"0.0.0.0"` + `auth_mode:"api_key"`),同 LAN 任意主机可达,仅靠 `${OPENVIKING_ROOT_API_KEY}` 鉴权。需要收口时用防火墙 / 绑定特定网卡,不要只依赖 api_key。
- 本机后端(仅监听宿主 `127.0.0.1`,不对外暴露):
  - **embedding**:`127.0.0.1:8021` 是一层零依赖 Python 透明代理(按请求体 `input_type` 为 query 时注入 qwen3 官方前缀),转发到内部 `127.0.0.1:8031` 的 llama-server(Qwen3-Embedding-0.6B,维度 **1024**)。
  - **reranker**:`127.0.0.1:8022`(llama-server,qwen3-reranker-0.6b)。
  - 两者由 systemd user service 管理:`qwen-llama@embedding-gpu`、`qwen-llama@reranker-gpu`。
- **vlm / query_planner**:远程 `https://api.kimi.com/coding/v1`(`kimi-for-coding`)。

## 配置与密钥机制

`ov.conf` 是 JSON。所有敏感字段用 `${VAR}` 占位,启动时由 openviking 的 `config_loader.py` 经 `os.path.expandvars` 展开(`$VAR` / `${VAR}` 均可)。真实值存 `secrets.env`(**已 gitignore,永不进 git**),`docker-compose.yml` 通过 `env_file` 注入容器环境。

- 改密钥:只改 `secrets.env` + `down/up`;`ov.conf` 里的占位符不动。
- 当前占位符:`${OPENVIKING_ROOT_API_KEY}`、`${OPENVIKING_VLM_API_KEY}`(embedding/rerank 是本地服务,`api_key` 用占位 `no-key`,无需外部化)。

## 常用命令

```bash
# 改任何配置(ov.conf / compose / secrets.env)后都必须 down/up —— 见坑 #1
docker compose down && docker compose up -d

docker compose ps
curl -fsS http://127.0.0.1:1933/health

# 端到端诊断:展开 ${VAR} + probe embedding/vlm 连通(最可靠的健康验证)
docker exec openviking openviking-server doctor

# 确认密钥已注入容器
docker exec openviking env | grep OPENVIKING

# qwen3_embed 后端(embedding/reranker)systemd user service
systemctl --user is-active qwen-llama@embedding-gpu qwen-llama@reranker-gpu
```

> 本仓库无 build / lint / test —— 它是部署配置,不是代码项目。验证手段是上面的 `doctor` + `health`。

## 运维脚本(手动版本锁定)

> **升级策略:不自动升级。** openviking 版本迭代快且偶有回归(如 v0.4.4 的 role.value 阻塞 bug,见坑 #11),自动追 `latest` 风险高。改为:**手动选版本 → 端到端测试确认(功能正常 + ov.conf 适配)→ 手动升级**。
>
> **何时才考虑升级**(当前钉在 v0.4.3,稳定可用就不动):① 新版本有我们需要的新特性;② 修复了我们在意的 v0.4.3 bug;③ v0.4.3 自己出现新的严重/阻塞性 bug。否则不升。**正式升级前必须先用新版本端到端测(E2E_TESTING.md §8),确认不引入阻塞**——v0.4.4 就是反面教材(带 role.value bug,doctor 还报 PASS)。

仓库 `scripts/` 目录下两个脚本(已 `chmod +x`):

- **`update-openviking.sh <tag>`** — **手动**升级到指定版本(**必填 tag**,如 `v0.4.3`;不再默认 `latest`)。流程:`sed` 把 docker-compose.yml 的 image 锁到 `openviking/openviking:<tag>` → `pull` → `up -d` → 轮询 `healthy` → `doctor` → 清悬空镜像;全程写 `update-openviking.log`(超 8 万行轮转)。**锁 tag 后 compose 不会被滚动的 latest 带走。** doctor 失败只告警不退出(依赖本机 qwen + 远程 kimi)。⚠️ doctor 的 `VLM: PASS` 不是 vlm 可用性判据(坑 #8),真实验证要端到端测入库/检索。
- **`cleanup-containers.sh`** — `docker compose down --remove-orphans` 停并移除容器。bind-mount 的 `workspace/`、`ov.conf` 不受影响。

> 原 `setup-cron.sh`(设定自动更新 cron)已删除——手动版本锁定策略下不再自动升级;若将来要恢复自动,`crontab -e` 加一行即可。

```bash
./scripts/update-openviking.sh v0.4.3   # 手动升级到 v0.4.3(必填 tag)
./scripts/cleanup-containers.sh         # 停并移除容器
```

> 两个脚本本身**进 git**(在 `scripts/`);它们写的 `update-openviking.log`、轮转临时 `*.tmp`、并发互斥锁 `.update-openviking.lock`(`update-openviking.sh` 用 flock 防止手动触发重叠)均已被 `.gitignore` 忽略。

## 关键坑(基于容器内源码核对 · 版本以 `openviking-server --version` 为准,本仓手动锁定、不自动追 latest,改配置前必读)

1. **改 `ov.conf` 后必须 `docker compose down && up -d`。** `restart`/`reload` 无效:ov.conf 是单文件 bind-mount,宿主文件被编辑器原子替换后 inode 变化,容器仍挂载旧 inode;`expandvars` 也只在启动读文件那一次执行。

2. **`expandvars` 对未设置的变量不报错,原样保留字面 `${VAR}`。** 若 `secrets.env` 没注入或变量名拼错,api_key 会停留在字面串:容器**照常启动且 healthy**(healthcheck 只查 `/health` 端口),但对应后端请求 401。**靠 `doctor` 或实际请求排查,不靠启动报错。**

3. **`rerank` 段不支持 `max_concurrent`**(也不支持任何额外字段 —— `RerankConfig` 用 `model_config={"extra":"forbid"}`)。在 rerank 段加字段 → pydantic 校验失败 → 容器起不来。`embedding`/`vlm`/`query_planner` 三段都支持 `max_concurrent`。

4. **改 embedding 维度必须重建 vectordb。** `workspace/vectordb/context/collection_meta.json` 存了 Dim,写入严格校验 `len(vector)==Dim`。重建 = 停容器后删 `workspace/vectordb/context` 再启动(容器以 root 写文件,删除需 root 或起临时容器)。当前维度 **1024**。

5. **rerank 的 `api_base` 是完整 endpoint URL**(`http://127.0.0.1:8022/v1/rerank`),客户端直接 POST,不是 base URL。

6. **embedding 段用 `provider:"openai"` 而非 `ollama`。** llama-server 是标准 OpenAI 兼容 API;`ollama` provider 会触发 openviking 的 ollama 自动检测/健康检查副作用。openai provider 有 `api_base` 即可,`api_key` 自动占位。

7. **非对称 embedding**:ov.conf 的 `embedding.dense` 用 `query_param:"query"` + `document_param:"document"` 启用。query 侧经 8021 代理注入官方前缀(实时),document 侧裸文本。改这个配置后**已入库的 document 向量无需重建**(两种方式余弦一致)。

8. **vlm/query_planner 必须显式设 `"temperature":1`。** kimi-for-coding 是强制 reasoning 模型,只收 `temperature=1`(<1 报 `only 1 is allowed for this model`;>1 报 `must not be greater than 1`);而 `VLMConfig.temperature` 默认 `0.0`,ov.conf 不覆盖 → openviking 实发 `0.0` → 每次 vlm/query_planner 真实 chat completion 必被 kimi 400。**容器照常 healthy,`doctor` 照报 `VLM: PASS`**(`doctor` 的 `check_vlm` 只校验配置/api_key 存在,**不发 chat**),极易潜伏(本仓曾因此静默挂了数天未被发现)。验证 vlm/query_planner 可用性**只能靠真实调用**,例如:`docker exec -i openviking python3 -c "import asyncio;from openviking_cli.utils.config import get_openviking_config as g;c=g();print(asyncio.run(c.vlm.get_completion_async('1+1=?')))"`,或直接 `curl` `/v1/chat/completions` 带 `temperature=1` 的最小请求。**`doctor` 的 `VLM: PASS` 不是 vlm 可用性的判据。**

  **另(vlm timeout):** vlm/query_planner 的 `timeout` 默认 `60s` 对 kimi reasoning 偏小 —— 文件级 summary / 目录级 overview 会超时(memory extraction 任务轻、勉强够)。本仓 `vlm=1200` / `qp=600`。超时表现:日志 `openai.APITimeoutError: Request timed out`、abstract 空(`(no abstract)`)、向量未建(search 召回但 score 0%)。

9. **vlm/query_planner 的 `max_tokens` 要给 reasoning 留余量(本仓 vlm=131072 / qp=65536,别退回默认 32768)。** kimi-for-coding 强制 reasoning(`supports_thinking_type:"only"`):每次输出 = `reasoning_content`(先)+ `content`(后),**共享 `max_tokens` 预算且 reasoning 优先消耗**;reasoning 把预算用尽时 `content` 还没生成就被 `finish_reason=length` 截断 → `content=""`。openviking 只取 `message.content`、丢弃 `reasoning_content` → **静默拿到空结果**(不报错、healthy、doctor PASS),文档照样入库但摘要/结构化字段为空、检索质量塌掉。根因:openviking 不认 kimi-for-coding 为 reasoning 模型(`_is_reasoning_model` 只认 `gpt-5/o1/o3/o4`),无"reasoning 吃 token 要预留"的逻辑;`KimiVLM` 默认 `max_tokens=32768` 偏小。`max_tokens` 是上限而非固定消耗,设大后日常成本/延迟不变,只抬高长 reasoning 的最坏上限(kimi 对该参数不卡上限;`context_length=262144` 才是 prompt+output 总预算的真正约束,大 prompt 时 output 实际上限被动态压低但不报错)。排查:vlm 返回空 / 日志 `finish_reason=length` 即被吃光;`reasoning_tokens` 字段 kimi 实测不单独返回,不可靠。**另:ov.conf 的 `thinking` 字段对 kimi provider 无效**(只对 `openai`+DashScope 往请求体加 `enable_thinking`),kimi-for-coding 强制 reasoning **无法关闭**;本仓 `vlm`/`query_planner` 设 `thinking:true` 仅反映此实际状态——改它不改变行为,也省不掉 reasoning 开销(想压成本只能靠更小的 `max_tokens` 限制思考上限,或换非 reasoning 模型)。

10. **`embedding.text_source`(默认 `content_only`)与 `max_input_tokens`(默认 4096)的非显而易见行为。** text_source 三值 `content_only`/`summary_first`/`summary_only`,但 **`summary_first` 与 `summary_only` 行为完全相同**(源码里总在同一 `in` 集合,无拼接/无差异),实际只有两种模式。摘要在**入库时无条件由 vlm 生成**(与 text_source 无关,用于 `.overview.md`/`.abstract.md`),故切 summary 模式**零额外 vlm 成本**——只是让向量复用已生成摘要。`content_only`:文本文件用正文、非文本(图片等)用摘要;summary 模式:文本文件用摘要(摘要为空则回退正文)。`max_input_tokens` 对所有送 embedding 的文本做**头部截断**(尾部拼 `...(truncated for embedding)`),与 text_source 无关——content_only 长文档只取头部约 `max_input_tokens` tokens。**改 text_source / max_input_tokens 只影响新入库文件,旧向量不变,要重算需走 reindex(不删 vectordb,维度没变)。** 另:dense 是语义检索、**不是字面匹配**,代码符号/精确关键词的精确查询是其结构弱项,需 sparse 才能补;而 **sparse/hybrid 当前只支持 `volcengine`/`vikingdb` 云 provider**(本地 `openai` provider 的 sparse/hybrid 是 `NotImplementedError`,无 local 实现),本地开源 sparse 模型(BGE-M3/SPLADE)暂接不进。

11. **v0.4.4 有 role.value 阻塞 bug,本仓已降级 v0.4.3。** v0.4.4 的 PR#2709 把 `class Role(str, Enum)` 改成 `class Role(str)`(移除 Enum),但所有 `ctx.role.value` 调用没更新 → memory extraction / resource summarization / forget 等全部 `AttributeError: 'str' object has no attribute 'value'` → **经 MCP 入库全崩**(无摘要无向量,search 全空;doctor/healthy 照常,是假象)。仅 v0.4.4 受影响(v0.4.3 及之前无此 bug);修复 PR#2728 已合并 main 但未发版(详见 GitHub issue #2718)。截至 2026-06-20,最新 release 仍是带 bug 的 v0.4.4,无 v0.4.5+;修复仅存在于 main,故继续钉 v0.4.3。**升级前务必端到端测入库(memory/resource),别只看 doctor;**下一个含 PR#2728 的版本发布后,按 `docs/E2E_TESTING.md` §8 测过再手动升级。

## 文件

- `docker-compose.yml` — host 网络 + `env_file: [secrets.env]` + 把 `ov.conf` 挂到容器 `/app/.openviking/ov.conf` + `workspace` 挂载。
- `ov.conf` — openviking 主配置(占位符版,**进 git**)。
- `docs/` — 专题文档目录(7 篇,均进 git):
  - `STORAGE_MODEL.md` — 三层存储模型与检索数据流(原文/摘要/向量分离,max_input_tokens 影响边界)。
  - `CONFIG_REFERENCE.md` — ov.conf 字段/默认值/provider 支持矩阵/extra:forbid 速查。
  - `SPARSE_HYBRID.md` — sparse/hybrid 架构现状、本地接入障碍、SOTA 模型、接入路径。
  - `KIMI_FOR_CODING.md` — vlm/query_planner 后端 kimi-for-coding 完整特性档案(K2.7 / temp=1 / 强制 reasoning)。
  - `E2E_TESTING.md` — 端到端测试方法与记录(MCP 测试架构、覆盖矩阵、副作用管控、升级前 checklist + 可复用脚本)。
  - `MULTI_USER.md` — 多 user(多租户)部署与使用指南(account/user/peer 模型、user key 隔离、admin API、各 MCP client 配置)。
  - `UPGRADE_0.4.md` — 0.3→0.4 升级变化(User/Peer 模型/legacy 迁移/多模态等)+ 本项目适配分析(legacy 残留处理 + 可选增强)。
- `secrets.env` — 真实密钥(**gitignore,不进 git**);`secrets.env.example` 是可提交的模板。
- `scripts/cleanup-containers.sh` / `scripts/update-openviking.sh` — 运维脚本(清理 / 手动升级),**进 git**;详见上文「运维脚本」。(原 `setup-cron.sh` 已删)
- `workspace/` — openviking 运行态数据(vectordb / queue / sessions / pid),容器以 root 写,**gitignore**。
