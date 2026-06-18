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

## 运维脚本(自动化)

仓库根目录下三个脚本(已 `chmod +x`),对标 ollama 仓的同名脚本:

- **`update-openviking.sh`** — 自动更新:`docker compose pull` → `up -d --remove-orphans` → 清悬空镜像,全程写 `update-openviking.log`(超 8 万行轮转)。**关键:`up -d` 后轮询容器到 `healthy`,再跑 `docker exec openviking openviking-server doctor` 做端到端验证**——healthcheck 只查端口(见坑 #2),不验证 `${VAR}` 展开 / 后端连通。doctor 失败只告警不退出(它依赖本机 qwen 后端 + 远程 kimi,任一未就绪都会失败,非镜像更新问题)。`up -d` 等同 down+up,顺带让 ov.conf/secrets.env 变更生效。
- **`cleanup-containers.sh`** — `docker compose down --remove-orphans` 停并移除 openviking 容器。bind-mount 的 `workspace/`、`ov.conf` 不受影响。
- **`setup-cron.sh`** — 幂等设定 crontab:每天 **6:30** 跑 `update-openviking.sh`(marker 去重)。时间错开 ollama 的 6:00,避免同机同时拉镜像 / 重建。

```bash
./setup-cron.sh          # 设定每天 6:30 自动更新(一次性)
./update-openviking.sh   # 立即手动更新一次
./cleanup-containers.sh  # 停并移除容器
```

> 三个脚本本身**进 git**;它们写的 `update-openviking.log`、轮转临时 `*.tmp`、并发互斥锁 `.update-openviking.lock`(`update-openviking.sh` 用 flock 防止 cron 与手动触发重叠)均已被 `.gitignore` 忽略。

## 关键坑(openviking 0.3.23 源码核对,改配置前必读)

1. **改 `ov.conf` 后必须 `docker compose down && up -d`。** `restart`/`reload` 无效:ov.conf 是单文件 bind-mount,宿主文件被编辑器原子替换后 inode 变化,容器仍挂载旧 inode;`expandvars` 也只在启动读文件那一次执行。

2. **`expandvars` 对未设置的变量不报错,原样保留字面 `${VAR}`。** 若 `secrets.env` 没注入或变量名拼错,api_key 会停留在字面串:容器**照常启动且 healthy**(healthcheck 只查 `/health` 端口),但对应后端请求 401。**靠 `doctor` 或实际请求排查,不靠启动报错。**

3. **`rerank` 段不支持 `max_concurrent`**(也不支持任何额外字段 —— `RerankConfig` 用 `model_config={"extra":"forbid"}`)。在 rerank 段加字段 → pydantic 校验失败 → 容器起不来。`embedding`/`vlm`/`query_planner` 三段都支持 `max_concurrent`。

4. **改 embedding 维度必须重建 vectordb。** `workspace/vectordb/context/collection_meta.json` 存了 Dim,写入严格校验 `len(vector)==Dim`。重建 = 停容器后删 `workspace/vectordb/context` 再启动(容器以 root 写文件,删除需 root 或起临时容器)。当前维度 **1024**。

5. **rerank 的 `api_base` 是完整 endpoint URL**(`http://127.0.0.1:8022/v1/rerank`),客户端直接 POST,不是 base URL。

6. **embedding 段用 `provider:"openai"` 而非 `ollama`。** llama-server 是标准 OpenAI 兼容 API;`ollama` provider 会触发 openviking 的 ollama 自动检测/健康检查副作用。openai provider 有 `api_base` 即可,`api_key` 自动占位。

7. **非对称 embedding**:ov.conf 的 `embedding.dense` 用 `query_param:"query"` + `document_param:"document"` 启用。query 侧经 8021 代理注入官方前缀(实时),document 侧裸文本。改这个配置后**已入库的 document 向量无需重建**(两种方式余弦一致)。

## 文件

- `docker-compose.yml` — host 网络 + `env_file: [secrets.env]` + 把 `ov.conf` 挂到容器 `/app/.openviking/ov.conf` + `workspace` 挂载。
- `ov.conf` — openviking 主配置(占位符版,**进 git**)。
- `secrets.env` — 真实密钥(**gitignore,不进 git**);`secrets.env.example` 是可提交的模板。
- `cleanup-containers.sh` / `update-openviking.sh` / `setup-cron.sh` — 运维脚本(清理 / 自动更新 / 设 cron),**进 git**;详见上文「运维脚本」。
- `workspace/` — openviking 运行态数据(vectordb / queue / sessions / pid),容器以 root 写,**gitignore**。
