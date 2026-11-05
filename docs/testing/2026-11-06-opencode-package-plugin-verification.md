# OpenCode package 插件迁移与 2026.11.5-1 验收记录

日期:2026-11-06
范围:OpenCode 2.2.5 的 OpenViking 插件安装方式、运行时实例健康、MCP 数据闭环、会话 commit、跨 peer 自动召回及定向清理。
身份边界:真实宿主目录 `/mnt/usr/project/learn/learn_leetcode`;所有 OpenViking 测试写入均使用 `default/test` USER、独立 peer 和进程 env 凭据,不写 meteor、不写共享区。
凭据纪律:测试 key 只从 `state/secrets/test_user_api_key` 读入进程环境;不创建第二份持久 key 配置,不在 argv、日志、报告或回复中输出明文。

## 1. 为什么迁移安装方式

2026-11-04 使用官方统一安装器后,OpenCode 0.5.24 以本地文件方式存在两个入口:

```text
~/.config/opencode/plugins/openviking/
~/.config/opencode/plugins/openviking.js
```

OpenCode 2.2.5 同时扫描目录与 wrapper。二者最终导出同一个 `id: openviking`,导致:

- 目录实例 `active`;
- wrapper 实例 `failed`,错误为 `Duplicate plugin ID: openviking`。

这解释了此前看似矛盾的现象:MCP 与功能测试可以通过,但 `/plugins` 界面仍显示一个插件失败。原验收只检查安装器摘要、MCP 和功能闭环,没有逐实例检查 `/api/plugin`,因此漏报了这一状态。包名和版本只说明 artifact 身份,不等于安装/加载方式。

用户随后手动完成迁移:

- 在 `opencode.jsonc` 的现有 `plugins` 数组中声明 `@openviking/opencode-plugin`;
- 删除旧的静态 `mcp.openviking` 本地绝对路径配置;
- 将上述两个本地入口移至 `~/.config/opencode/plugins_bak/` 备份。

package 插件通过自身 `pluginRoot` 自动注册 MCP proxy,无需手写 OpenCode package cache 路径,也不再引用旧本地副本。

## 2. 迁移后的静态与运行时基线

| 项目 | 实测结果 |
|---|---|
| OpenCode | v2.2.5 |
| 插件声明 | `@openviking/opencode-plugin` |
| 实际 package 版本 | `2026.11.5-1` |
| package `openvikingSourceCommit` | `ed3fe9166d0dbce27f33abba16a78d83ec9c06d6` |
| `/api/plugin` 实例数 | 1 |
| `/api/plugin` 来源/状态 | `type=package`,`status=active` |
| `/api/mcp` | `openviking: connected` |
| 当次 MCP 连接工具数 | 24 |
| 运行中 proxy | OpenCode package cache 内的 `servers/mcp-proxy.mjs` |
| 旧本地文件 | 保留在 `plugins_bak/`,不在插件扫描目录中 |

未固定为旧版 0.5.24,因此本次不仅改变了安装机制,也升级了插件。2026-11-04 的完整功能结果不能直接视为新版本的验收结果,需要本报告中的重新验证。

## 3. test 身份与进程环境

执行前 `bootstrap_auth.py check-user` 返回 `default/test/user`,临时健康检查配置已删除。新插件的连接配置经只读断言为:

```text
baseUrl=http://127.0.0.1:1933
account=default
user=test
peerId=agent-e2e-package-5e19fd3c4c
credentialSource=env
apiKeySource=env
hasApiKey=true
timeoutMs=300000
clientVersion=2026.11.5-1
```

关键变量为 `OPENVIKING_CREDENTIAL_SOURCE=env`、`OPENVIKING_URL`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_PEER_ID`、`OPENVIKING_API_KEY` 与 `OPENVIKING_TIMEOUT_MS`。该方式避免为测试另写一份 key 配置,也避免 managed 服务继承不到 CLI 身份覆盖的问题;真实 OpenCode 会话均使用 `--standalone`、显式模型并关闭 stdin。

## 4. MCP write/read/find/forget

通过 package 内的真实 stdio MCP proxy 完成 initialize → notifications/initialized → tools/list → tools/call。serverInfo 为 `openviking/1.27.0`,当次 24 个工具。

专用资源:`viking://user/test/resources/opencode-package-20261106-5e19fd3c4c.md`
专用 marker:`PACKAGE-DATA-5e19fd3c4c`

| 步骤 | 实际结果 |
|---|---|
| write | 写入 63 字节;返回 `semantic=complete, vector=complete` |
| read | 按当前 schema 使用 `uris: [uri]`,读回唯一 marker |
| find | 命中专用资源 URI;当次也生成过父级语义 overview |
| forget | 返回 `Deleted: <专用 URI>` |
| direct read 复核 | 404 |

### 4.1 当前 schema 与旧假设的差异

- `read` 必填参数是 `uris` 数组,不是 `uri`。
- `write` 必填 `uri`、`content`;当前 schema 没有 `processing_mode`,不能沿用 direct API 的参数假设。实际返回语义与向量索引均完成。
- `find` 必填 `query`,可选 `target_uri/limit/min_score/...`。
- `forget` schema 为 `uri` 与 `recursive`;direct API 的 `wait=true` 不应未经核对就当作 MCP schema 字段。

首轮验收脚本因把 read 写成 `uri` 而得到参数校验错误;失败路径已执行 forget。修正 schema 后才得到上述完整通过结果。更早一次 JavaScript 正则语法错误发生在解析阶段,未启动 proxy、未产生任何 OpenViking 写入。二者均属于验收脚本问题,不计为插件功能失败。

## 5. 会话捕获与 commit

真实 standalone plan 会话:

```text
OpenCode session: ses_e5311c08dffe7DF2l0v3Ra6FbN
OpenViking session: oc-ses_e5311c08dffe7DF2l0v3Ra6FbN
peer: agent-e2e-package-5e19fd3c4c
```

会话只写入一条明确标注为测试事实的 recall marker,事件流没有工具调用,回复为:

```text
CAPTURE_OK PACKAGE-RECALL-5e19fd3c4c
```

插件日志在退出时记录:

```text
Committed OpenViking session
reason=flushAll
openviking_session=oc-ses_e5311c08dffe7DF2l0v3Ra6FbN
```

随后 direct `POST /api/v1/search/find` 首次请求即返回 HTTP 200 且命中 marker,相关 URI 为:

```text
viking://user/test/peers/agent-e2e-package-5e19fd3c4c/memories/events/2026/11/05/插件验收信标指定.md
viking://user/test/peers/agent-e2e-package-5e19fd3c4c/memories/events/2026/11/05/.overview.md
```

因此 commit 不只存在于客户端日志,也已形成可检索的服务端事件记忆。

## 6. 无工具跨 peer 自动召回

第二个全新 standalone plan 会话使用不同 peer `agent-e2e-package-recall-5e19fd3c4c`,并设置:

```text
OPENVIKING_AUTO_CAPTURE=0
OPENVIKING_RECALL_PEER_SCOPE=all
```

提示词不包含 marker 答案,要求不调用任何工具。实际结果:

- 事件流没有工具调用;
- 返回 `RECALL_OK PACKAGE-RECALL-5e19fd3c4c`;
- 插件记录 `recall_context_assembled`,6 个条目、1452 tokens,tiers 为 `abstract:4 + overview:2`;
- 该会话没有同 peer `session-inject` 记录,结果不是由同一 peer 的会话存档注入解释;
- 没有 recall 会话的 commit 日志,符合关闭 auto capture 的设置。

不过 `fs/ls` 仍发现 recall session 的 `messages.jsonl`。因此“关闭 auto capture”不等于“不会产生任何 session 文件”,清理必须检查 `viking://user/test/sessions/`,不能只检查 commit 日志。

## 7. 定向清理与最终状态

以下五个目标均 DELETE 200,删除后 content read / fs list 均为 404 或无剩余匹配:

1. 专用 resource;
2. `peers/agent-e2e-package-5e19fd3c4c`;
3. `peers/agent-e2e-package-recall-5e19fd3c4c`;
4. capture 的 `oc-` session;
5. recall 的 `oc-` session。

最终复核:

- data marker:`find` HTTP 200,`markerInDocument=false`,无 marked URI;
- recall marker:`find` HTTP 200,`markerInDocument=false`,无 marked URI;
- resources、sessions、memories 与 test 根级 `.abstract/.overview` 均不含两个 marker;
- peer 下事件文件与 overview 已删除;
- 插件状态目录没有 pending queue 文件;正常日志和 session-state 文件保留;
- 初始化模板与其他 test 数据未修改;
- OpenCode 自身本地会话/数据库记录未删除;本次删除的是 OpenViking 服务端专用 session 数据。

## 8. 验收经验(后续必须保留)

1. **先核对实例健康,再核对功能。**`/api/plugin` 应只有一个预期来源且 active 的 OpenViking 实例;`/api/mcp` connected 和 24 工具不能证明另一个同名实例没有 failed。
2. **包名不等于加载方式。**报告必须明确 local file、wrapper、npm package 或 marketplace archive,并记录实际 proxy 路径。
3. **package 模式下不要保留旧静态 proxy 路径。**让插件从自己的 `pluginRoot` 注册 MCP,避免升级后继续运行旧副本。
4. **工具参数以当次 schema 为准。**特别是 read 的 `uris` 数组;不能从 direct API 或旧版本记忆推断 MCP 参数。
5. **真实宿主测试仍须强制 test 身份。**`OPENVIKING_CREDENTIAL_SOURCE=env` 配合完整 URL/account/user/peer/key 可避免临时 key 配置;OpenCode 使用 standalone,因为 managed 服务不会继承 CLI 覆盖。
6. **清理要覆盖派生产物。**除目标资源外,还须检查 peer 记忆、两个 session 目录及 `.abstract/.overview`;最后重新 find 两个 marker,而不是只看 DELETE 200。
7. **不得把历史 PASS 外推到新版本。**本次 package 为 `2026.11.5-1`;后续插件、proxy 或 Server 任一升级都必须重新记录版本并复验。

## 9. 文档更新后的项目回归

- `python3 -m pytest -q`:**198 passed**(16.35s)。
- `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`:**13/13 PASS**,包括 USER 的常规数据面可用与账户管理面拒绝。
- `git diff --check`:通过;文档变更未修改生产代码。
