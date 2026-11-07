# Agent 接入:Claude Code 与 OpenCode 2.x(活文档)

> 定位:两个正式 Agent 工具的接入基线、逐实例验收方法与 test 身份宿主复验流程。操作值以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

正式 Agent 共用宿主 `~/.openviking/ovcli.conf` 中的 meteor USER key,沿用官方按
仓库派生 peer 的机制;不逐工具创建 user/key/peer,也不把 default/root key 放进
Agent。当前基线为 Claude Code 2.1.316 + `openviking-memory@openviking` 0.7.21、
OpenCode v2.2.5 + `@openviking/opencode-plugin` **2026.11.5-1**。版本升级后须重新验收。

## OpenCode:package 插件与自动 MCP 注册

当前 OpenCode 使用 package 插件。将包名合并进现有 `plugins` 数组,不要覆盖其他插件:

```jsonc
{
  "plugins": [
    "@openviking/opencode-plugin"
    // 保留宿主已有的其他 package 插件
  ]
}
```

OpenCode v2 也兼容官方 `plugin` 写法的规范化,但本机配置已使用原生 `plugins`,不要
同时维护两套键。**不要手工保留 `mcp.openviking` 的本地绝对路径**:package 插件会从
自己的 `pluginRoot` 自动注册 package 内的 `servers/mcp-proxy.mjs`,URL、身份和 key 仍由
`ovcli.conf` 或 `OPENVIKING_*` 提供。

2026-11-04 的一键安装器曾同时写入 `plugins/openviking/` 与 `plugins/openviking.js`;
在 OpenCode 2.2.5 中二者被重复扫描,目录实例 active、wrapper 实例以
`Duplicate plugin ID: openviking` 失败。用户已于 2026-11-06 将这两个入口移至
`~/.config/opencode/plugins_bak/` 并迁移到 package。重跑旧安装流程前须先确认其生成物,
不得重新引入多个同名入口。

验收不能只看 MCP connected 或安装器摘要:还须核对 `/api/plugin` 中只有一个预期来源
且 active 的实例、`/api/mcp` 为 connected,并确认实际 proxy 路径指向当前 package。
Claude Code 则使用 URL marketplace 的 archive 插件 `openviking-memory@openviking`,当前
marketplace `autoUpdate=true`;其实际升级同样不能免验收。

## test 身份下的真实宿主复验

自动捕获/召回实验默认在沙箱 HOME 中使用 test 身份,不使用 meteor 的真实个人数据。
经用户明确许可在真实宿主目录验收时,也必须通过进程环境覆盖为 test key 和独立 peer;
OpenCode 使用 `--standalone`,因为 managed 服务不会继承 CLI 的身份覆盖环境,同时显式
关闭 stdin 并选择已配置的模型,避免等待输入或默认免费模型限额。可使用纯 env 连接,
避免另写临时 key 配置:

```bash
export OPENVIKING_CREDENTIAL_SOURCE=env
export OPENVIKING_URL=http://127.0.0.1:1933
export OPENVIKING_ACCOUNT=default
export OPENVIKING_USER=test
export OPENVIKING_PEER_ID=agent-e2e-package-<random>
export OPENVIKING_API_KEY="$(< state/secrets/test_user_api_key)"
export OPENVIKING_TIMEOUT_MS=300000
# 在获批准的测试目录中,选择已配置模型并关闭 stdin
opencode --standalone run --model zhipuai-coding-plan/glm-5.3-flash '...' </dev/null
```

MCP 参数以当次 schema 为准:当前 read 使用 `uris` 数组,write schema 未声明
`processing_mode`,不能假定它与 direct API 参数一致。清理除专用资源外,还须检查 peer 记忆、session 目录及自动生成的
`.abstract/.overview`,最后重新 find 测试 marker。完整迁移、schema、commit、跨 peer
自动召回与清理结果见
[OpenCode package 插件验收](../testing/2026-11-06-opencode-package-plugin-verification.md);
2026-11-04 的[身份与 Agent 接入验收](../testing/2026-11-04-user-identity-and-agent-integrations.md)
保留为历史基线。
