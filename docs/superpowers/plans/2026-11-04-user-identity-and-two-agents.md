# OpenViking 多身份与 Claude Code/OpenCode 接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 default 运维身份的前提下落地 test/meteor 两个 USER 身份、验证双向私有隔离，并只接入和验收 Claude Code 与 OpenCode 2.x。

**Architecture:** 将现有 `bootstrap_auth.py` 泛化为创建、轮换、临时配置健康检查的单一认证实现；shell 测试共用不加载密钥的 curl helper。真实数据面验收使用 direct curl；Agent 正式配置使用 meteor，写入实验在沙箱 HOME 中切换到 test。

**Tech Stack:** Python 3 标准库、pytest、Bash、curl、Docker Compose、OpenViking v0.5.2.1、Claude Code、OpenCode 2.x、官方统一安装器。

**Spec:** `docs/superpowers/specs/2026-11-01-user-identity-and-multi-agent-plan.md`

## Global Constraints

- 任何 key、root key、token 明文均不得进入工具输出、回复、argv、git 或测试日志；敏感响应体默认丢弃。
- 测试夹具使用沙箱 HOME；需要 git 身份时只在沙箱仓库局部配置，禁止 `git config --global`。
- test 只在 `state/secrets/test_user_api_key` 持久保存一份 600 key；meteor 不在 repo 另存 key，只写宿主 `~/.openviking/ovcli.conf`（600）。
- 临时 CLI 配置放容器 `/tmp` 或沙箱，显式含 URL，不含 root key，使用前后均清理；不得依赖 restart 清除 `/tmp`。
- 数据写入优先 test 私有区；不写 `viking://agent/*`；共享区仅做基线读取，专用私有夹具必须删除并复核。
- 本方案只验收普通数据面的协作式隔离，不宣称消除同 UID、共享区、ADMIN/ROOT 或服务端 `users.json`/备份的访问面。
- Agent 范围仅 Claude Code 与 OpenCode 2.x；不接入 pi、DSH、Hermes、OpenClaw，不逐工具创建 user/key/peer。
- 不擅自升级 host 工具或修改无关全局配置；官方安装器使用 `--no-statusline`，只允许其写入本方案明确列出的 OpenViking 集成配置。
- 默认直接提交 main，提交信息以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾；不 push。

## Review Focus

- 本地落盘失败但服务端已创建 user：不得再次 POST 创建，只能修复目标后轮换；由 Task 1 的恢复测试覆盖。
- test/meteor 临时配置缺 root key、缺 URL 或继承 root 环境：必须 fail-closed；由 Task 1 的健康/环境净化测试覆盖。
- 跨 user 读取可能返回 403 或 404：两者都按不可见接受，但自有读写必须 200；由 Task 2 的隔离测试覆盖。
- 官方安装器部分成功且仍退出：不能以退出码或 “Server check passed” 判完成；由 Task 5 逐项核对安装摘要、插件和配置。
- 沙箱缺少 Claude/OpenCode 模型登录态：不得复制真实凭据或改用真实 HOME 写测试数据；由 Task 6 的显式阻塞门禁覆盖。

---

### Task 1: 泛化用户创建、轮换和身份健康检查

**Files:**
- Modify: `scripts/bootstrap_auth.py`
- Modify: `tests/_http_fakes.py`
- Test: `tests/test_bootstrap_auth.py`

**Interfaces:**
- Produces: `_register_user(base_url, root_key, user_id, role, timeout=30)` 与 `_rotate_user_key(base_url, root_key, user_id, timeout=30)`。
- Produces: `provision_user(root_key, base_url, user_id, role, *, user_key_path=None, cli_config_path=None, timeout=30) -> int`；两个输出路径恰好指定一个，分别写裸 key 或含 `url/api_key/account/user` 的 600 JSON 配置。
- Produces: `rotate_user(root_key, base_url, user_id, *, user_key_path=None, cli_config_path=None, timeout=30) -> int`，复用同一输出函数。
- Produces: `_ov_health_ok(..., base_url=DEFAULT_BASE_URL, *, expected_account, expected_user, expected_role, require_root_key, saved_profile_path) -> str`；`saved_profile_path=None` 表示临时配置无 saved 检查对象。
- Produces: `_saved_profile_path(cli_config_path, profile_name="local")` 与 `_configure_cli(..., profile_name="local")`；任何非 default 持久配置均不得隐式复用 `local`。
- Produces: CLI 子命令 `provision-user`、`rotate-user`、`check-user`；无参数调用仍执行原 default bootstrap。

- [ ] **Step 1: 写泛化与恢复失败测试**
  覆盖 test/user 创建请求、600 原子写入、已有有效本地 key 不创建、409 不重建、创建后落盘失败、修复后只走轮换、meteor JSON 不引入 root key、独立 profile 名不覆盖 `ovcli.conf.local`，以及 default 无参数回归。
- [ ] **Step 2: 写健康检查失败测试**
  覆盖 `default/test/user` 通过、root 角色失败、缺 URL 失败、临时配置出现 root 字段失败、saved 检查显式跳过、继承 ROOT 环境被移除、超时仍区别于 invalid。
- [ ] **Step 3: 运行测试确认新用例失败**
  Run: `python3 -m pytest tests/test_bootstrap_auth.py -q`；Expected: 新泛化用例 FAIL，既有用例作为对照。
- [ ] **Step 4: 实现通用接口并保持 default 包装路径**
  `_register_default_user()` 调用 `_register_user(..., "default", "admin")`；root-free 临时配置的 `_cfg_matches` 不得默认补 URL，default 旧配置保留文档化默认地址兼容；`require_root_key=False` 时禁止 root 字段；`_run_ov` 在 root_key 为 None 时删除继承的 ROOT 环境。创建成功但写入失败只给轮换恢复路径，不重复创建。
- [ ] **Step 5: 实现临时 check-user 配置与 CLI 子命令**
  `check-user` 从 key 文件读取 USER key，在 `/tmp` 或调用者指定沙箱路径写 600 临时配置，净化 root 环境，新 HOME 缺语言设置时先复用 `_ensure_cli_language`，核对显式 account/user/role，finally 删除；所有错误只打印步骤/退出码。
- [ ] **Step 6: 运行完整单测**
  Run: `python3 -m pytest tests/test_bootstrap_auth.py -q`；Expected: 全部 PASS，输出无凭据。
- [ ] **Step 7: 提交 Task 1**
  `git add scripts/bootstrap_auth.py tests/_http_fakes.py tests/test_bootstrap_auth.py docs/superpowers/plans/2026-11-04-user-identity-and-two-agents.md && git commit`，信息为 `feat: 泛化用户身份初始化与健康检查` 并带规定署名。

### Task 2: 抽取安全 curl 库并实现双向私有隔离测试

**Files:**
- Create: `tests/curl_auth.sh`
- Create: `tests/test_curl_auth.py`
- Create: `tests/e2e_user_isolation.sh`
- Modify: `tests/e2e_auth_matrix.sh`

**Interfaces:**
- Produces: `code_auth <header-line> <curl-args...>`，仅经 stdin `-K -` 传 header，curl 首参 `-q`，丢弃 body，只输出 HTTP 状态。
- Produces: `tests/e2e_user_isolation.sh`，输入 `USER_A`、`USER_B`、`USER_A_KEY_FILE`、`USER_B_KEY_FILE` 与 loopback `BASE`；使用 `viking://user/<uid>/resources/identity-isolation-<random>.md` 专用夹具。

- [ ] **Step 1: 写 fake-curl 测试**
  用含引号/反斜杠的假 key 验证 key 不在 argv、stdin 转义正确、响应体不输出、无 ROOT/ADMIN 环境加载。
- [ ] **Step 2: 运行 fake-curl 测试确认失败**
  Run: `python3 -m pytest tests/test_curl_auth.py -q`；Expected: 因 `tests/curl_auth.sh` 不存在而 FAIL。
- [ ] **Step 3: 抽取 helper 并改造认证矩阵**
  矩阵 source `tests/curl_auth.sh`；保持现有 ROOT/ADMIN/USER 状态断言与 `REQUIRE_USER=1` 行为不变。
- [ ] **Step 4: 实现 direct-curl 隔离闭环**
  以 `POST /api/v1/content/write`（`mode=replace`、`wait=true`、`processing_mode=vectors_only`）写双方私有夹具；自有 read 为 200，交叉 read 为 403|404；`DELETE /api/v1/fs?uri=...&recursive=true&wait=true` 以 300 秒超时清理并复核。trap 尽力清理，失败即非零，不打印响应体。
- [ ] **Step 5: 运行离线测试和矩阵基线**
  Run: `python3 -m pytest tests/test_curl_auth.py -q && bash tests/e2e_auth_matrix.sh`；Expected: fake-curl PASS；尚无 test key 时矩阵 USER 行只允许 SKIP。
- [ ] **Step 6: 提交 Task 2**
  `git add tests/curl_auth.sh tests/test_curl_auth.py tests/e2e_user_isolation.sh tests/e2e_auth_matrix.sh && git commit`，信息为 `test: 增加用户私有区双向隔离验收` 并带规定署名。

### Task 3: 创建 test、完成身份与数据面验收并更新 README

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-11-01-user-identity-and-multi-agent-plan.md`
- Create: `docs/testing/2026-11-04-user-identity-and-agent-integrations.md`

**Interfaces:**
- Consumes: Task 1 的 `provision-user`/`check-user` 与 Task 2 的隔离脚本。
- Produces: `state/secrets/test_user_api_key`（600，不入 git）及不含凭据的验收记录。

- [ ] **Step 1: 建立敏感恢复点与非敏感基线**
  按 README tar 命令备份，不回显文件内容；将 `resources`/`agent` 的 simple listing 保存到权限受控临时文件，记录服务版本/digest 和 git HEAD。
- [ ] **Step 2: 创建 test**
  在容器内运行 `bootstrap_auth.py provision-user --user-id test --role user --key-file /app/.openviking/secrets/test_user_api_key`；root key 只从既有容器环境使用，输出不得含 key。
- [ ] **Step 3: 验证 default 与 test 身份**
  default 跑无参数 bootstrap 幂等路径；test 跑 `check-user`，断言 `default/test/user` 且临时配置删除。任一为 root 或身份不符即停止。
- [ ] **Step 4: 跑强制 USER 矩阵和双向隔离**
  Run: `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`，再以 default/test key 文件运行隔离脚本；Expected: 自有读写成功、交叉不可见、双方夹具清理成功。
- [ ] **Step 5: 比对共享区并回归**
  比较 `resources`/`agent` 基线，运行完整 pytest；只有明确需要模型链路的部署回归才运行 smoke，并在报告中记录实际执行/跳过项。
- [ ] **Step 6: 更新文档**
  README 写三身份表、key/备份边界、临时 test 配置和 direct-curl 纪律，移除持久 test profile 默认建议；方案状态记录 2026-11-04 已批准且 Agent 仅 Claude Code/OpenCode 2.x；测试报告只记身份、状态码和清理结果。
- [ ] **Step 7: 提交 Task 3**
  `git add README.md docs/superpowers/specs/2026-11-01-user-identity-and-multi-agent-plan.md docs/testing/2026-11-04-user-identity-and-agent-integrations.md && git commit`，信息为 `docs: 记录三身份边界与 test 隔离验收` 并带规定署名。

### Task 4: 创建 meteor 并验证与 test 的双向隔离

**Files:**
- Modify: `docs/testing/2026-11-04-user-identity-and-agent-integrations.md`

**Interfaces:**
- Consumes: Task 1 的 `provision-user` JSON 输出与 Task 2 的隔离脚本。
- Produces: 宿主 `~/.openviking/ovcli.conf`（600），无 root 字段，无 repo 裸 key 副本。

- [ ] **Step 1: 执行宿主配置门禁**
  用 Python 只输出布尔字段和权限；配置不存在可直接继续，存在则须 JSON 可解析且无 `root_api_key`，否则停止请求单独迁移批准。
- [ ] **Step 2: 创建 meteor 并直接写宿主配置**
  root key 经变量从 `secrets.env` 加载，宿主运行 `provision-user --user-id meteor --role user --cli-config "$HOME/.openviking/ovcli.conf"`；不得使用 `--api-key` argv。
- [ ] **Step 3: 验证 meteor 身份**
  将只含 meteor URL/account/user/api_key 的临时 600 JSON 经 stdin 写入容器 `/tmp`，运行 `ov health -o json` 并断言 `default/meteor/user`，随后删除并复核。
- [ ] **Step 4: 验证 meteor/test 隔离**
  meteor key 由 Python 从宿主 JSON 经 process substitution 提供，test key 从既有文件读取；运行 Task 2 隔离脚本并确认双方夹具清理。
- [ ] **Step 5: 记录结果并提交报告**
  只写身份、版本、验收状态与清理结论，不写 key；提交报告，信息为 `docs: 记录 meteor 与 test 隔离验收` 并带规定署名。

### Task 5: 只安装 Claude Code 与 OpenCode 2.x 官方集成

**Files:**
- Modify: `docs/testing/2026-11-04-user-identity-and-agent-integrations.md`
- External approved outputs: `~/.openviking/`、`~/.claude/` 中 OpenViking plugin/marketplace 配置、`~/.config/opencode/` 中 OpenViking 配置。

**Interfaces:**
- Consumes: Task 4 的 meteor 宿主配置。
- Produces: 两工具正式集成及版本/artifact/hash/备份清单。

- [ ] **Step 1: 固定 host 基线并做配置快照**
  记录 Claude Code/OpenCode 精确版本；OpenCode 必须为 2.x。将涉及配置复制到新建 700 临时目录，不打印内容，不覆盖其他备份。
- [ ] **Step 2: 下载并记录安装器**
  `install_dir=$(mktemp -d)`，`curl -fsSL https://openviking.ai/install -o "$install_dir/bootstrap.sh"` 成功后计算 SHA-256；若与已审查版本不同，先静态复核再运行。
- [ ] **Step 3: 一次批量安装两个 harness**
  从宿主既有 meteor 配置取 key，命令只含 `--yes --lang zh --harness claude,opencode --no-statusline`，不传 `--api-key`；不附加 cli 或其他 Agent。
- [ ] **Step 4: 核对部分失败与凭据边界**
  检查安装摘要无 NOT_DONE、Claude plugin/marketplace 与 OpenCode plugin/MCP 均加载；宿主 ovcli 仍为 600 且无 root；相关备份不得出现宽松权限。失败则保留现场并按快照恢复，不反复盲跑。
- [ ] **Step 5: 记录安装基线并提交报告**
  记录安装器、插件 artifact hash、host 版本、OpenViking marketplace autoUpdate 实际状态及安装器明确写入的配置；不修改其他全局 auto-update 设置。

### Task 6: 沙箱验收 MCP、数据闭环和跨工具记忆共享

**Files:**
- Modify: `docs/testing/2026-11-04-user-identity-and-agent-integrations.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 5 的官方 artifact 与 test 身份。
- Produces: 两工具逐项 PASS/FAIL 证据、清理结果和兼容基线；不以插件列表或健康探测替代功能验收。

- [ ] **Step 1: 建立完全沙箱化测试环境**
  新建临时 HOME、git 工作区和 OpenViking 配置目录；test key 经变量写入沙箱 600 ovcli 配置。git origin 指向不访问网络的专用标识；如需提交只在仓库局部设置身份。
- [ ] **Step 2: 在沙箱加载同一官方集成**
  用沙箱 HOME、`CLAUDE_CONFIG_DIR`、`XDG_CONFIG_HOME` 和 `OPENVIKING_*` 指向沙箱；仅复制/加载 OpenViking 官方 artifact，不复制真实宿主认证或完整配置。若模型登录态缺失，停止并请用户在沙箱交互登录，不改真实 HOME。
- [ ] **Step 3: 分别验收 Claude Code 与 OpenCode MCP**
  新会话完成真实 handshake 和 tools list，保存当次工具名/数量；随后通过 Agent 实际调用 read/find/write/delete，服务端用 test key 核对专用 URI 的结果。
- [ ] **Step 4: 验证生命周期与双向跨工具召回**
  在同一 test user/仓库 peer 写入两条唯一非真实测试事实：Claude 捕获后由 OpenCode 新会话召回，OpenCode 捕获后由 Claude 新会话召回；核对 commit/退出与离线重放结果及 cc-/oc- 会话前缀。
- [ ] **Step 5: 清理并复核**
  删除专用资源、记忆和临时配置，检查 pending queue/测试会话残留及共享区基线；删除沙箱。任何清理失败都在报告中明确记 FAIL，不宣称完全隔离。
- [ ] **Step 6: 完成文档和最终回归**
  README 只列 Claude Code/OpenCode 2.x 接入与复验入口；报告记录精确版本、hash、autoUpdate、MCP 清单和实际执行结果。运行完整 pytest 与 `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`。
- [ ] **Step 7: 最终提交**
  `git add README.md docs/testing/2026-11-04-user-identity-and-agent-integrations.md && git commit`，信息为 `docs: 完成 Claude Code 与 OpenCode 接入验收` 并带规定署名；不 push，最后报告所有未执行或失败项。
