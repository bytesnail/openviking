# OpenViking 用户身份与 Agent 接入验收记录

日期:2026-11-04  
范围:test/meteor 用户身份、私有区隔离,以及 Claude Code 与 OpenCode 2.x 官方集成。  
授权:用户当次明确指示开始执行,并审阅实施计划后选择本会话直接执行。  
凭据纪律:本报告不含任何 key/token;命令输出仅保留身份、状态码、版本和非敏感路径。

## 1. 环境基线与恢复点

- OpenViking Server:v0.5.2.1,镜像 digest `sha256:7c26f9777c7632fd4b771be77471634bffb6ec8d6338b39f53deb2da1b1d34b3`。
- 实施起点:`f5520efe4b9f209e8ccba99aace4de0a271eb30e`;基线测试 174 passed。
- 敏感恢复点:`backups/openviking-20261104-010500.tar.gz`,权限 600;仅在用户明确批准 `state/`(含 secrets 与 users.json)和 `secrets.env` 写入该本地备份后创建,未上传。
- 共享区基线:以 default USER key 的常规数据面分别保存 `viking://resources`、`viking://agent` 的 recursive simple listing;不在报告中展开资源内容。
- 预检:Claude Code `2.1.316`,OpenCode `v2.2.5`;宿主当时不存在 `~/.openviking/ovcli.conf`,test key 文件不存在。

## 2. test 身份落地结果

| 检查 | 实际结果 |
|---|---|
| 创建 test | Admin API 创建 `test`,role=user;未创建账户或重复 user |
| test key 本地副本 | 仅 `state/secrets/test_user_api_key`,权限 600,不入 git |
| default 幂等健康检查 | `default/default/admin`,healthy=true |
| test 临时 CLI 健康检查 | `default/test/user`,healthy=true;临时配置仅含 test USER key,无 root 字段 |
| 临时配置清理 | `/tmp/ovcli.test.conf` 验证后不存在 |
| 强制认证矩阵 | `REQUIRE_USER=1`:9 项基础边界 + 4 项 USER 边界全部通过 |
| default 写/读自身私有夹具 | 200 / 200 |
| test 写/读自身私有夹具 | 200 / 200 |
| default 读取 test 私有夹具 | 403,按不可见通过 |
| test 读取 default 私有夹具 | 403,按不可见通过 |
| default/test 夹具删除 | 均 200;删除后各自 read 均 404 |
| 共享区事后比对 | `resources` 与 `agent` 均与实施前 JSON 完全一致 |
| 离线/单元回归 | 192 passed |

测试写入使用 `/api/v1/content/write`,`mode=replace`、`wait=true`、
`processing_mode=vectors_only`;读取使用 `/api/v1/content/read`,删除使用
`DELETE /api/v1/fs`,`recursive=true`、`wait=true`。删除等待沿用 CLI 的 300 秒
超时。未写共享区、未写 `viking://agent/*`、未写记忆。

## 3. 实施中发现并修正的问题

1. **default 旧配置缺省 URL**:真实 `ov health` 使用文档化默认地址并返回
   `default/default/admin`,但首版泛化一致性检查要求所有配置显式 URL,导致
   bootstrap 误判 RC=4。修正为:仅 default/ADMIN 旧配置保留默认地址兼容;
   root-free 临时配置仍强制显式 URL。新增回归测试后 default/test 均通过。
2. **初版计划的数据面路径不准确**:镜像源码确认 content 路由前缀为
   `/api/v1/content`,不是 `/api/v1/fs`。错误路径的写入返回 404,未创建资源;
   假服务与脚本同步修正后,最终真实验收通过。
3. **删除等待超过 15 秒**:初次真实 DELETE 返回 curl 000(客户端超时),随后
   read 已为 404,说明删除生效但等待未完成。删除请求单独采用 300 秒超时后,
   最终 DELETE 200、删除后 read 404。
4. **模型 smoke 未运行**:本阶段变更限于认证与测试脚本,不改模型、适配器或
   服务部署;按 README 的变更面规则运行完整 pytest 与认证/身份验收,未额外
   消耗 GLM Coding Plan 用量。

## 4. 当前隔离结论边界

本验收只证明同账户普通数据面上的 user 私有区双向不可读与专用夹具清理。它不
消除以下访问面:同 UID 进程对本机 key/users.json 的读取、default ADMIN/root
的管理面间接访问、共享区写入、服务端与备份中的明文 key 副本,以及账户级
模型/token/向量库/任务队列残留。不得据此宣称测试对任何身份“零影响”。

## 5. meteor 身份验收结果

| 检查 | 实际结果 |
|---|---|
| 宿主前置门禁 | 原 `~/.openviking/ovcli.conf` 不存在,无旧 root 字段迁移问题 |
| 创建 meteor | Admin API 创建 `meteor`,role=user;未重复创建 |
| 宿主配置 | 直接写入 `~/.openviking/ovcli.conf`,权限 600,显式 URL,无 root_api_key |
| repo 副本 | 未在 `state/secrets/` 或其他 repo 路径保存 meteor 裸 key |
| meteor 健康检查 | `default/meteor/user`,healthy=true |
| meteor 临时容器配置 | 经 stdin 写入 `/tmp`,仅含 meteor USER key;验证后删除并复核 |
| meteor/test 自有写入与读取 | 双方均 200 / 200 |
| meteor 读取 test 私有夹具 | 403,按不可见通过 |
| test 读取 meteor 私有夹具 | 403,按不可见通过 |
| 双方夹具清理 | DELETE 均 200,删除后 read 均 404 |
| 共享区复核 | resources/agent 均与初始基线一致 |

meteor key 通过宿主 JSON 的 process substitution 进入隔离脚本,未新增持久副本。
为支持这一无副本路径,隔离脚本的 key 输入门禁由“普通文件且非空”修正为
“来源可读且读取后非空”,新增 `/dev/fd` 离线回归测试。

## 6. Claude Code 与 OpenCode 2.x 安装基线

| 项目 | 实际结果 |
|---|---|
| 安装范围 | 单次 `--harness claude,opencode`,未附加 cli/其他 Agent,未启用 statusline |
| 安装器 | bootstrap 5297 字节,SHA-256 `951b42cf83f83c5912c46609324fba1b2d2d526a67ca6a1779c4628061ca958e`,与 2026-11-02 审查副本一致 |
| 安装摘要 | exit=0,NOT_DONE=0,error/failed=0 |
| Claude Code | host 2.1.316;`openviking-memory@openviking` 0.7.21,user scope,enabled |
| Claude marketplace | OpenViking URL marketplace;`extraKnownMarketplaces.openviking.autoUpdate=true` |
| OpenCode | host v2.2.5;`@openviking/opencode-plugin` 0.5.24;plugin 与 MCP server 均已注册 |
| meteor 凭据边界 | ovcli.conf 保持 600、default/meteor、无 root_api_key;未通过 argv 传 key |
| 配置变更边界 | Claude settings 仅新增 OpenViking enabledPlugin 与 marketplace 配置,无无关路径变化 |
| 备份权限 | ovcli/opencode 备份均为 600;OpenCode 备份初始为 664,发现后已收紧 |

目录聚合指纹(排序后的相对路径与文件内容):
- `~/.openviking/memory-plugin-marketplace`:429 文件,SHA-256 `f515928a16b8feac99fb2b5c8369298a04b3764732f0a71957b2fd1c8051f900`。
- `~/.config/opencode/plugins/openviking`:54 文件,SHA-256 `5ffbf1c2fc964145e770cb5fa082cb2e3f621a5be120da7b393cbbbe5c87244e`。

以上只证明安装与加载前静态/CLI 状态,不代替真实 MCP 与数据面验收。

## 7. MCP、数据闭环与跨工具召回验收

待沙箱功能验收完成后追加;未执行或未通过的项目不得写成完成。
