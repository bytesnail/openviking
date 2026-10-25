# OpenViking v0.5.2 本机 Docker Compose 部署设计

日期：2026-10-25
状态：设计已确认，实施尚未开始
目标目录：`/mnt/usr/tools/openviking`
调研证据：[OpenViking 官方与本机调研](../../research/2026-10-25-openviking-official.md)

## 1. 目标与成功标准

在目标目录提供一套可复现的 Docker Compose 部署，满足：

1. OpenViking 使用最新核心 release **v0.5.2**，镜像固定 tag 和 manifest digest。
2. 复用 `/mnt/usr/tools/model-serving` 已运行的 `embeddinggemma-2` 与 `qwen3-vl-reranker-2b`，不修改其代码、监听地址或生命周期。
3. 启用本机 embedding 的文本与多模态向量能力；通过适配器解决 OpenViking v0.5.2 与 llama.cpp 当前请求外壳的不兼容。
4. VLM 使用 Z.AI GLM Coding Plan 的 **`glm-5.3-flash`**，endpoint 为 `https://api.z.ai/api/coding/paas/v4`。
5. `secrets.env` 提供 `Z_AI_API_KEY`；真实凭据不出现在配置示例、日志、命令输出或对话中。
6. 仅供本机单用户使用：API、适配器和既有模型服务均只监听 `127.0.0.1`，不提供局域网入口。
7. 使用 `api_key` 模式，避免 loopback 上的无认证 ROOT 访问；root key 与 User/Admin key 分离。
8. 数据、客户端配置和初始化状态可持久化、备份和恢复。
9. 验收覆盖真实模型调用、认证、导入和检索；不能用 liveness 或静默 fallback 冒充模型成功。

## 2. 已确认决策

| 决策点 | 选择 |
|---|---|
| 访问范围 | 本机单用户 |
| 网络 | 两个 service 均使用 host 网络，强制 loopback，不配置 `ports` |
| OpenViking 版本 | `v0.5.2`，实际 tag commit `7f3ae1af822d936c1d0b070537772b0f1d59122f` |
| VLM provider/model | 原生 `glm` provider，`glm-5.3-flash`，不使用 `[1m]` |
| Embedding | 启用 `input: "multimodal"`，经 loopback 协议适配器访问 18351 |
| Reranker | OpenAI-compatible 完整 endpoint `/v1/rerank` |
| 输入预算 | embedding/rerank 初始均为 4096 estimated tokens，不直接写满 8K |
| 并发 | embedding 与 VLM 均为 2 |
| 认证 | `api_key`；root key 管理账户，User/Admin key 访问数据 |
| VikingBot | 不启用 |
| Caddy | 不创建 legacy Caddy service |
| 工作区 | 全新 `./state`，不迁移旧 OpenViking 数据 |

### 2.1 版本基线

镜像：

```text
ghcr.io/volcengine/openviking:v0.5.2@sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd
```

调研时该 digest 同时是 `latest` 的指向，但部署不依赖 `latest`。release notes 中的另一个 “Release target” 哈希不作为源码基线；以实际 tag `v0.5.2` / `7f3ae1a…` 为准。

### 2.2 非目标

本方案不包括：

- 局域网、公网、多用户、HTTPS 或反向代理；
- VikingBot 聊天 Agent、渠道凭据、Direct exec 或 OpenSandbox；
- 修改 `model-serving` 的 host、鉴权、模型文件或 systemd 配置；
- OpenViking/Claude Code 风格的 1M context 自动 compaction；
- 未经验证把输入预算提升到 8K；
- 既有 OpenViking workspace 的升级迁移；
- Coding Plan 不可用或不受条款支持时自动改换其他付费 API。

## 3. 架构与数据流

```text
本机 Studio / ov / SDK
        │ User/Admin key
        ▼
127.0.0.1:1933  OpenViking（api_key，VikingBot 关闭）
        │
        ├─ embedding ─▶ 127.0.0.1:18353  多模态协议适配器
        │                         └─▶ 127.0.0.1:18351  embeddinggemma-2
        │
        ├─ rerank ────────────────────▶ 127.0.0.1:18352  qwen3-vl-reranker-2b
        │
        └─ VLM ───────────────────────▶ api.z.ai/api/coding/paas/v4
                                        model=glm-5.3-flash
```

### 3.1 OpenViking service

- 使用 host 网络，通过 `OPENVIKING_SERVER_HOST=127.0.0.1` 和 `server.host` 双重固定监听边界。
- 不配置 `ports`；1933 直接存在于宿主机 loopback。
- `OPENVIKING_WITH_BOT=0`，不启动 18790 Gateway。
- 官方 entrypoint 和 healthcheck 保持不变。
- `./state` 挂载到 `/app/.openviking`；`storage.workspace: "./data"` 对应宿主 `./state/data`。
- `./config/ov.conf` 只读挂载到 `/app/.openviking/ov.conf`。配置变更通过宿主文件编辑和重启完成，不依赖 Studio 写回配置。
- 只有此 service 读取 `secrets.env`。

### 3.2 Embedding 适配器 service

适配器解决以下已实测差异：

- OpenViking：`input: [text_part, image_part]`，当前返回 HTTP 400。
- 本机 llama.cpp：`input: [{"content": [text_part, image_part]}]`，返回一个 768 维向量。

适配器职责严格限定为：

1. 在 `127.0.0.1:18353` 提供 `POST /v1/embeddings`。
2. 对非空且全部为受支持 typed parts 的多模态 `input`，包装为 `[{"content": parts}]` 后转发到 `http://127.0.0.1:18351/v1/embeddings`。
3. 纯文本、文本批处理和已经符合目标形态的请求原样转发。
4. 对混合且含义不明确、包含不支持 part 类型或请求正文超过 **64 MiB** 的输入返回 4xx，不猜测转换。
5. 多模态响应必须恰好包含一个向量；否则返回 502，不允许 OpenViking 静默取第一项。
6. 提供 `/health` 和 `/ready`；`/ready` 检查上游 `/health`，不发起推理。
7. 不记录请求正文、图片、向量、Authorization header 或响应正文。
8. 不读取 `secrets.env`，不挂载 `state`，不需要 GLM 或 reranker 访问权限。
9. 上游调用超时固定为 120 秒，业务转发并发上限为 2；不使用无界线程或无界请求队列。

复用同一 OpenViking 镜像中的 Python 标准库，避免新增基础镜像。适配器使用只读根文件系统、非 root numeric UID/GID、`cap_drop: ALL`、`no-new-privileges` 和小型 tmpfs；这些限制必须在实施验收中验证。

## 4. 目标文件布局

```text
/mnt/usr/tools/openviking/
├── compose.yaml
├── config/
│   └── ov.conf
├── scripts/
│   ├── prepare.sh
│   ├── embedding_adapter.py
│   ├── bootstrap_auth.py
│   └── smoke_models.py
├── tests/
│   ├── fixtures/
│   │   ├── smoke.md
│   │   ├── red.png
│   │   └── blue.png
│   ├── test_embedding_adapter.py
│   └── test_bootstrap_auth.py
├── state/                       # 运行时生成；敏感，属主 1000:1000，权限 700
│   ├── .openviking-compose-v1   # prepare 创建的本方案状态标记
│   ├── data/                    # OpenViking workspace
│   ├── secrets/
│   │   └── user_api_key         # 初始化生成，权限 600
│   └── ovcli.conf               # 初始化生成，权限 600
├── secrets.env                  # 已存在；加入本地 root key 后权限 600
├── backups/                     # 本地受控备份的目标目录，权限 700
├── .gitignore
├── README.md
└── docs/
    ├── research/
    │   └── 2026-10-25-openviking-official.md
    └── superpowers/specs/
        └── 2026-10-25-openviking-compose-design.md
```

`backups` 是本地受控备份目录，不上传、不纳入版本控制。若后续改用其他备份位置，运行手册必须同样保证密钥与状态数据不会进入对话或不安全位置。

目标 `.gitignore` 至少包含：

```gitignore
/.env
/secrets.env
/state/
/backups/
__pycache__/
.pytest_cache/
```

`.env` 仅预留给非秘密、机器特定的 Compose 设置；当前设计不要求它存在，真实密钥只能放在 `secrets.env`。

## 5. Compose 设计

目标 `compose.yaml`：

```yaml
name: openviking

x-openviking-image: &openviking-image
  ghcr.io/volcengine/openviking:v0.5.2@sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd

services:
  embedding-adapter:
    image: *openviking-image
    network_mode: host
    user: "65532:65532"
    entrypoint: ["python", "-I", "-B", "/opt/openviking/scripts/embedding_adapter.py"]
    environment:
      EMBEDDING_ADAPTER_HOST: "127.0.0.1"
      EMBEDDING_ADAPTER_PORT: "18353"
      EMBEDDING_UPSTREAM_URL: "http://127.0.0.1:18351/v1/embeddings"
      EMBEDDING_UPSTREAM_TIMEOUT_SECONDS: "120"
      EMBEDDING_MAX_BODY_BYTES: "67108864"
      EMBEDDING_MAX_CONCURRENT: "2"
    volumes:
      - ./scripts:/opt/openviking/scripts:ro
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=16m
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test:
        - CMD
        - python
        - -I
        - -B
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18353/ready', timeout=3).read()"
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 10s
    restart: unless-stopped

  openviking:
    image: *openviking-image
    network_mode: host
    user: "1000:1000"
    env_file:
      - ./secrets.env
    environment:
      OPENVIKING_SERVER_HOST: "127.0.0.1"
      OPENVIKING_SERVER_PORT: "1933"
      OPENVIKING_WITH_BOT: "0"
      OPENVIKING_CONFIG_FILE: "/app/.openviking/ov.conf"
      OPENVIKING_CLI_CONFIG_FILE: "/app/.openviking/ovcli.conf"
    volumes:
      - ./state:/app/.openviking
      - ./config/ov.conf:/app/.openviking/ov.conf:ro
      - ./scripts:/opt/openviking/scripts:ro
      - ./tests/fixtures:/opt/openviking/fixtures:ro
    depends_on:
      embedding-adapter:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "openviking-entrypoint", "--healthcheck"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: unless-stopped
```

约束：

- 不添加 `ports`、Caddy 或 VikingBot service。
- 不给适配器添加 `env_file`。
- 不使用 `latest`。
- 主服务以当前宿主用户 `meteor` 的 numeric UID/GID `1000:1000` 运行，使 `state`、CLI 配置和备份保持同一属主；`prepare.sh` 必须核对实际 UID/GID，其他用户部署时先显式调整 Compose，不能错配权限。
- 实施必须验证官方 entrypoint、native engine、healthcheck 和状态写入均能在该非 root 用户下工作；若上游存在不兼容，返回设计评审，不能静默改回 root。
- 不让 Compose 在缺少 `ov.conf` 时把挂载源自动创建成目录；`prepare.sh` 必须先验证文件类型。
- 1933、18353 在首次启动前必须空闲；18351/18352 必须已由 `model-serving` 提供。

## 6. `ov.conf` 设计

目标 `config/ov.conf`：

```json
{
  "storage": {
    "workspace": "./data"
  },
  "embedding": {
    "max_concurrent": 2,
    "max_retries": 3,
    "max_input_tokens": 4096,
    "dense": {
      "provider": "openai",
      "model": "embeddinggemma-2",
      "api_base": "http://127.0.0.1:18353/v1",
      "api_key": "local-not-used",
      "dimension": 768,
      "input": "multimodal",
      "encoding_format": "float"
    }
  },
  "vlm": {
    "provider": "glm",
    "model": "glm-5.3-flash",
    "api_base": "https://api.z.ai/api/coding/paas/v4",
    "api_key": "${Z_AI_API_KEY}",
    "max_concurrent": 2,
    "max_retries": 3,
    "timeout": 600
  },
  "rerank": {
    "provider": "openai",
    "model": "qwen3-vl-reranker-2b",
    "api_base": "http://127.0.0.1:18352/v1/rerank",
    "api_key": "local-not-used",
    "timeout": 60,
    "max_input_tokens": 4096,
    "threshold": 0.1,
    "log_payloads": false
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "auth_mode": "api_key",
    "root_api_key": "${OPENVIKING_ROOT_API_KEY}"
  }
}
```

### 6.1 参数理由

- `dimension: 768` 来自本机真实 embedding 响应；OpenAI provider 不向 llama.cpp 发送 `dimensions` 参数。
- `encoding_format: "float"` 与本机已验证响应一致。
- 本地 `api_key` 只是非秘密占位值，满足 OpenViking 客户端校验，不是凭据。
- `rerank.api_base` 必须是完整 `/v1/rerank` endpoint。
- `rerank.max_input_tokens: 4096` 避免默认 0 导致长输入直接超过本机 8K；query/document 按 OpenViking 规则截断。
- 4096 是保守初始值，不是精确 tokenizer 保证。后续调升必须以真实 token 和模板开销测试为依据。
- `vlm.max_concurrent: 2` 避免默认 32 路造成 Coding Plan 突发压力。
- 不配置 `vlm.max_tokens`；它只控制输出，与 1M context 无关。
- 不配置 `query_planner`，按官方行为复用 VLM。
- 不配置 `reasoning_effort` 或 `extra_request_body`，先验证最小兼容配置，再依据实际语义抽取质量调整。

### 6.2 上下文边界

OpenViking v0.5.2 不解析 `[1m]`，没有 VLM `context_window` 字段，也不读取 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`。GLM 模型能力与 OpenViking 的 prompt 预算是两层：

- Z.AI 负责模型实际上下文能力；
- OpenViking 各资源理解和 session 路径分别控制输入分批、预览和 token 预算。

因此，本方案不宣称“OpenViking 已开启 1M 管理”。

## 7. 凭据与认证初始化

### 7.1 `secrets.env`

现有变量：

```text
Z_AI_API_KEY=<现有值，不读取到对话>
```

新增本地变量：

```text
OPENVIKING_ROOT_API_KEY=<prepare.sh 随机生成，不输出>
```

`prepare.sh` 必须：

1. 使用不执行文件内容的解析方式检查变量；不得 `source secrets.env`。
2. 检查 `Z_AI_API_KEY` 存在、非空且不是纯 X 占位符。
3. 若 root key 缺失，使用密码学安全随机数生成 32 字节 hex；若变量已存在但为空，失败而不是覆盖。
4. 采用追加或原子写入，保留已有 Z.AI key，不回显任何值；重复执行不重复添加变量。
5. 将 `secrets.env` 权限收紧为 600。
6. 检查展开后的 JSON 可解析，且两个密钥位置没有被保留为未展开的 `${...}` 字面值；错误输出不得包含密钥。
7. 以当前宿主用户的 UID/GID 创建或校验 `state`、`state/secrets`、`backups`，目录权限为 700，并核对与 Compose 的 `1000:1000` 一致。
8. 在全新空 `state` 中创建 `.openviking-compose-v1` 标记；后续执行必须验证标记。若 `state` 已包含非本方案创建的数据，停止并要求先备份和人工确认，不直接接管或清空。

### 7.2 Account/User 初始化

`bootstrap_auth.py` 在 OpenViking healthy 后于容器内执行：

1. 从环境读取 root key，不打印。
2. 若 `state/secrets/user_api_key` 与 `state/ovcli.conf` 均存在，先验证现有认证；有效则幂等退出。
3. 首次初始化通过 root key 调用：

   ```http
   POST /api/v1/admin/accounts
   {"account_id":"default","admin_user_id":"default"}
   ```

4. 从响应提取 `result.user_key`，原子写入容器 `/app/.openviking/secrets/user_api_key`，宿主路径为 `state/secrets/user_api_key`，权限 600。
5. 使用 `ov config add custom --name local --url http://127.0.0.1:1933 --activate -o json`，并通过 `--api-key-env` / `--root-api-key-env` 让 CLI 从子进程环境读取密钥；捕获输出，不转发可能含敏感信息的响应。
6. 确保 `state/ovcli.conf` 权限 600，并用 `ov health -o json` 验证认证。
7. 如果 default account 已存在但本地 User key 缺失或失效，停止并进入人工恢复/轮换流程；不得重复创建账户、伪造恢复成功或打印管理响应。

Studio 使用 User/Admin key 访问数据；root key 仅用于管理。用户需要在本地自行读取相应文件完成 Studio 登录，不能把密钥粘贴到对话。

## 8. 准备、启动与日常操作

README 运行手册提供以下流程，实际命令在实施阶段生成并验证：

```bash
cd /mnt/usr/tools/openviking
./scripts/prepare.sh
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose exec -T openviking python -I -B /opt/openviking/scripts/bootstrap_auth.py
```

注意：

- 只使用 `docker compose config --quiet`，不输出完整渲染配置。
- 不运行容器内 `env`/`printenv`，不输出完整 `docker inspect`。
- 查看日志前确认不会包含配置或密钥；自定义脚本统一捕获并脱敏错误。
- `docker compose down` 保留宿主 `state`；不执行任何清理 `state` 的命令作为普通重启步骤。
- Studio：`http://127.0.0.1:1933/studio`。
- API：`http://127.0.0.1:1933`。

## 9. 验证与完成判据

### 9.1 静态与单元验证

1. `docker compose config --quiet` 成功。
2. `ov.conf` 在沙箱假密钥环境下完成变量展开、JSON parse 和 OpenViking schema 校验。
3. 适配器单元测试覆盖：
   - 纯文本和文本批处理不变；
   - OpenViking 多模态 parts 只增加一层 `content`；
   - 已包装输入不重复包装；
   - 非法 part、混合输入和超大输入被拒绝；
   - 多模态多向量/空向量响应返回 502；
   - 上游状态和错误被安全处理，日志不含正文。
4. 认证初始化测试使用临时沙盒 HOME、临时 CLI 配置和假 HTTP server，覆盖首次、重试、已有账户但丢 key、无效 key；不得写真实 HOME 或真实凭据。
5. 所有测试夹具使用假密钥；不得通过 `git config --global` 或修改真实 dotfiles 构造环境。

### 9.2 本机模型与适配器验证

1. `model-serving/bin/serve.sh status` 显示两个模型运行。
2. 适配器文本请求与直连 18351 的文本结果维度一致，语义结果无异常变化。
3. OpenViking 形态的多模态请求经适配器返回 HTTP 200 和恰好一个 768 维向量。
4. 同一图片经适配器与直接 `content` 包装请求的向量一致或 cosine 在测量容差内接近 1。
5. 对“红色方块”查询，红色图片向量的相似度必须高于蓝色图片，防止只验证形状而未验证视觉语义。
6. 非法多模态响应不会被静默降级为文本向量。

### 9.3 OpenViking 与模型验收

```bash
docker compose exec -T openviking openviking-server doctor
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py
docker compose exec -T openviking ov health -o json
curl -fsS http://127.0.0.1:1933/health
curl -fsS http://127.0.0.1:1933/ready
```

完成标准：

- doctor 全部通过；其 embedding probe 返回 768 维。
- `smoke_models.py` 直接完成：
  - GLM 文本 Chat Completions；
  - GLM 图像 Chat Completions；
  - OpenViking rerank client 的真实请求，并验证相关文档得分高于无关文档。
- 脚本只输出状态和非敏感指标，不输出密钥或完整模型响应。
- `ov health -o json` 明确包含 healthy=true，并验证 auth accepted，不能只看命令退出码。
- `ss` 确认 1933、18353、18351、18352 仅监听 loopback，18790 未由本部署启动。
- VLM 两次真实调用会产生少量 Coding Plan 使用量；实施执行前需得到执行确认，不在后台自动重试成功为止。

### 9.4 端到端验收

1. 用 `ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/deployment-smoke --wait` 导入文本夹具。
2. 导入 `red.png` 到 `viking://resources/deployment-smoke-image --wait`，检查任务完成及生成内容，不把 VLM placeholder/fallback 当成功。
3. `ov find` 查询文本夹具中的独特事实，要求对应资源成为首要相关结果。
4. 检查生成 overview/abstract 和任务状态，确认没有隐藏的模型失败。
5. 冒烟资源默认保留供检查；清理前先 `ov ls` 核实 URI，再单独执行明确删除，不在部署脚本中自动递归删除。

只有静态、模型、认证、网络和端到端检查全部通过，才能声明部署成功。

## 10. 失败处理

| 故障 | 行为 |
|---|---|
| Z.AI/root key 缺失或为空 | prepare 失败，不启动；不依赖 expandvars 后的非空字面占位符 |
| 1933/18353 被占用 | prepare 失败并报告端口，不杀既有进程 |
| 18351/18352 不健康 | 适配器 `/ready` 或 preflight 失败；不修改 model-serving |
| 多模态响应数量异常 | 适配器返回 502，OpenViking 不得取向量列表第一项 |
| VLM model/key/endpoint 被拒绝 | smoke 失败，部署不判成功；不自动切换模型或普通 API |
| rerank 请求失败 | 直接 smoke 失败；不能因搜索回退 vector scores 而判成功 |
| default account 已存在但 User key 丢失 | bootstrap 停止，进入人工恢复/轮换，不重复创建 |
| 配置修改 | 编辑宿主 `config/ov.conf`，重启并重新运行 doctor 与模型 smoke；不假设热加载 |
| 状态目录存在未知数据 | prepare 停止，先备份并确认，不清空 |

## 11. 备份、升级与回滚

### 备份

1. `docker compose stop`，停止新写入。
2. 将 `state/`、`config/`、`secrets.env`、`compose.yaml` 和 `scripts/` 保存到权限 700 的 `backups/` 下带时间戳的归档；归档权限 600。
3. 备份包含敏感数据，不打印内容、不上传、不纳入版本控制。
4. 恢复演练必须在独立临时目录中使用独立的 Compose project name，并在配置副本中显式更换 1933/18353 等监听端口；不得覆盖或连接正在使用的生产状态。

### 升级

1. 先备份。
2. 人工选择新 release，核对官方 tag、对应源码和镜像 digest；不直接拉 `latest` 覆盖。
3. 更新 Compose 的 tag+digest，检查 `ov.conf` schema 和适配器协议是否仍适用。
4. 启动后重新执行全部模型、认证和端到端验收。

### 回滚

1. 停止服务并保留当前异常状态，不直接删除。
2. 检查恢复目标后，恢复与旧镜像匹配的配置和状态备份。
3. 使用旧 tag+digest 启动并重跑验收。
4. 不只回滚镜像而保留可能不兼容的新状态。

Embedding 模型或维度变化不作为普通升级处理；必须使用官方迁移/reindex 流程或新建空工作区，不能靠 `allow_metadata_override` 掩盖语义漂移。

## 12. 安全与条款说明

- host 网络只是拓扑选择，不代表开放网络；实际边界由 loopback 绑定和 `api_key` 共同维持。
- 适配器不持有任何真实密钥，主服务密钥通过 `env_file` 注入；`ov.conf` 只保存变量引用。
- `state/ovcli.conf` 和 `state/secrets/user_api_key` 都是敏感文件，备份时同等保护。
- 不启用 VikingBot，避免默认 Direct exec、额外 Gateway 和额外 GLM 调用。
- Coding Plan 官方文档限定订阅用于其支持的工具/产品，当前列表未列 OpenViking。OpenViking 提供 `glm` backend 仅证明协议接入能力，不证明 Z.AI 订阅授权。若需要严格遵循该范围，应改用适当的 Standard API 产品并重新确认 endpoint/key，而不是把本方案视为条款豁免。
