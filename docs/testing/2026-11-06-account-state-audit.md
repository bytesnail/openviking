# 2026-11-06 全账户状态审计记录

日期:2026-11-06
范围:本机 OpenViking v0.5.2.1 部署的 default 账户下全部三个用户(default/meteor/test)与共享区(`resources/`、`agent/`、`system/`)的状态核查,分路径级(含隐藏条目)与内容级两层。
身份边界:审计以 meteor 身份(MCP 工具)查看自身与共享区;test 与 default 私有区经各自 USER key 只读列举,不写任何数据。
凭据纪律:key 一律经进程环境变量中转(docker compose exec -e),容器内生成临时 CLI 配置(umask 177/set -C,600,用后即删),明文不出现在命令文本、argv 或输出中;`state/secrets/` 两个 key 文件未 cat/Read。

## 1. 审计方法

| 视角 | 途径 |
|---|---|
| meteor 自身 + 共享区 | openviking-memory MCP(list/tree/read,auto-recall 关闭判断不影响列举) |
| test / default 私有区 | 容器内 `ov ls -r -s -a`(递归 + simple + 含隐藏)、`ov read`、`ov grep -i`,临时配置同 `bootstrap_auth.py check-user` 模式 |
| 账户存在性 | `state/data/viking/` 目录列举(本地,不触凭据) |
| 用户清单 | meteor 视角 `list_users`(user_id 级,无凭据) |

## 2. 身份面

| 检查项 | 实况 | 判定 |
|---|---|---|
| 账户 | `state/data/viking/` 仅 `default` 一个账户目录 | PASS |
| 用户 | 恰好 `default`(admin)、`meteor`(user)、`test`(user) | PASS(与 README 三身份表一致) |
| key 文件 | `state/secrets/{user_api_key,test_user_api_key}` 均 600 | PASS |
| 服务 | `/health` OK(43ms),storage: VikingFS | PASS |

## 3. 共享区

- `viking://resources/`:仅 `deployment-smoke/smoke.md` 与 `deployment-smoke-image/red.png`,README 明确保留供检查。
- `viking://agent/`:`endpoints/`、`payments/`、`skills/`、`tools/` 四个空骨架(spec §2.1 服务端命名空间),无人违反"测试不写 `agent/*`"。
- `viking://system/`:仅自带模板(`compile/example-skills`、`memory/memory_templates`)。

## 4. 用户私有区(路径级)

- **default**:纯空骨架(各子目录仅 `.abstract/.overview`),运维身份未写数据面。
- **test**:初始化骨架;`memories/` 含 `identity.md`、`soul.md`、空 `entities/`;无 `agent-e2e-*` peer、无 `oc-`/`cc-` session、无专用测试资源——2026-11-06 package 验收第 7 节五项清理全部保持已删。
- **meteor**:初始结构 + `sessions/cc-47083f26-…/messages.jsonl`(见 §6)+ auto-capture 提取的 `memories/` 与 `peers/` 内容。

## 5. test 私有区内容级深核

全部 16 个 `.md` 文件逐一 `ov read` 过目:

- `identity.md`:未填写的原始模板(`Name` 空、"Fill this in…" 提示原样),无写入痕迹;
- `soul.md`:官方通用模板原文;
- `peers/`、`resources/`、`sessions/` 概览:SemanticProcessor **`trigger: content_delete`** 重建,`freshness: total_entries: 0`,正文如实描述空目录——清理后重建的直接证据;
- `memories/`、`privacy/`、`skills/` 及根级概览:初始化通用结构描述。

`ov grep -i` 特征词硬扫描(范围 `viking://user/test`,7 模式全部零命中):

| 模式 | 覆盖目标 |
|---|---|
| `PACKAGE-` | 2026-11-06 两个 marker 前缀 |
| `agent-e2e`、`oc-ses` | 测试 peer、OpenCode 测试会话前缀 |
| `CAPTURE_OK`、`RECALL_OK` | 两轮验收会话回显暗号 |
| `e2e`、`marker` | 通用测试词网 |

## 6. meteor 会话存档归属判定

`sessions/cc-47083f26-…/messages.jsonl` 经两条独立证据确认**为当时进行中的审计会话本身**:

1. 本地 transcript `~/.claude/projects/-mnt-usr-tools-openviking/47083f26-….jsonl` 与之 UUID 一致且正被实时写入;
2. 存档首条 user 消息与会话首个提问逐字一致,后续事件序列(Skill 调用、`!node ov-status.mjs`、ToolSearch)一一对应;`peer_id` 为按仓库路径派生的 `-mnt-usr-tools-openviking`。

结论:auto-capture 在会话**进行中**即向服务端实时写入 session 存档与实体/偏好记忆(事件记忆待退出 flush),meteor 私有区 memories/peers 非空属正常使用数据,不构成残留。

## 7. 经验要点

1. **路径级之后必须做内容级**:`.abstract/.overview` 是 LLM 生成物,可能嵌入历史测试词汇;概览 frontmatter 的 `trigger` 与 `freshness` 字段是判读清理后状态的直接证据。
2. **marker 复核用 `ov grep` 而非仅语义 find**:字面/模式搜索不受语义漂移影响;模式集应覆盖历次验收的 marker 前缀、会话前缀(`cc-`/`oc-`)与测试 peer 前缀。
3. **会话归属判定**:本地 transcript 文件名(UUID)匹配 + 首条消息内容匹配,双证据即可定论。
4. **审计自身遵守凭据纪律**:容器内临时配置模式(600、noclobber、用后删)可直接复用 `check-user` 的实现;key 经 `-e` 环境变量传入容器,不进 argv。
5. **实体记忆存在"写完即过时"的自指风险**:以"meteor 私有区仅有 sessions"为基线落盘的瞬间,该描述即被 auto-capture 新写入的记忆否定;基线类表述应写为"含 X、Y、Z 且随使用增长"的开放式,而非封闭清单。本次已同步修正实体记忆 `openviking_local_deployment`。

## 8. 结论

所有账户与共享区处于符合预期的干净状态:三用户与预期身份一致、共享区仅预期的两个冒烟资源与系统骨架、test 为初始化模板 + 清理后零条目概览、meteor 仅含本会话自身与 auto-capture 产物。无任何历次验收测试残留。
