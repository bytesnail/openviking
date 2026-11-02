# OpenViking 官方统一安装器：下载链路、参数与副作用静态审查

- **研究/访问日期**：2026-11-02（脚本响应头时间为 2026-11-01T18:34:48Z 起）。
- **研究对象**：`curl -fsSL https://openviking.ai/install` 当前取得的 bootstrap、其后续 shared installer、版本解析响应与 release 插件包；官方仓库对应源码和 curl 官方手册。
- **方法约束**：只做静态源码/文档分析和下载指纹固定。未执行安装器或其中任何代码，未执行三身份方案，未创建/轮换用户或 key，未重启部署，未读取本机真实凭据。下载文件均放在各自独立的新临时目录；未在下载目录运行解释器或构建工具。
- **核心源码基准**：线上 shared installer 的构建提交 [`6b2bcf29b2f4169b519dd96015b50adb3dca08f2`](https://github.com/volcengine/OpenViking/commit/6b2bcf29b2f4169b519dd96015b50adb3dca08f2)，下称“构建 commit”。除特别说明外，本文安装器行号均指向该 commit；线上文件与其只有版本行差异，故行号一致。

## 结论速览

1. `curl -fsSL https://openviking.ai/install` **不带管道时只把 bootstrap 文本写到 stdout，不安装、不执行**；接上 `| bash ...` 后才由 bash 执行下载内容。`-s` 只关闭进度/消息，不禁止正文输出。[curl `-s` 官方定义](https://curl.se/docs/manpage.html#-s)
2. 当前链路是 `openviking.ai/install` → 并行请求 `docs.openviking.net/dl` 与 `docs.openviking.ai/dl` 下的 `memory-plugin-shared/install.sh` → 首个通过基本标题检查的副本被 bash 执行。线上 shared installer 版本为 **`20261101-6b2bcf29b2`**。[bootstrap 固定源码 L39-L55、L59-L119](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/bootstrap.sh#L39-L119)
3. 安装器安装的是 **agent 插件、hooks/MCP/skills 和可选 `ov` CLI**，不是 OpenViking Server；默认连接已有 `ovcli.conf`，否则假设 `http://127.0.0.1:1933` 且空 key。运行前已有可用服务是官方集成前提。[官方集成总览 L36-L38](https://github.com/volcengine/OpenViking/blob/4b92b30656c821712e2cf5f053963dedc66206d9/docs/en/agent-integrations/01-overview.md#L36-L38)、[安装器默认连接 L1600-L1640](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1600-L1640)
4. 有 TTY 且未给 `--yes` 时交互；**无 TTY 时即使未给 `--yes` 也按非交互默认值继续安装**。没有 `--dry-run`；审查应下载后阅读，不要把“会打印计划”误当成只预览。[交互判定 L310-L327](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L310-L327)、[主流程 L3822-L3832](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3822-L3832)
5. 服务探测通过只表示 `/api/v1/system/status` 返回 2xx；**USER、ADMIN、ROOT key 都可能通过，但 ROOT 在 API Key 模式不能访问 tenant 数据或作为正常 MCP 数据 key**。探测失败也不会自动中止安装。[探测逻辑 L1650-L1700](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1650-L1700)、[v0.5.2.1 ROOT 路径规则 L17-L35、L203-L217](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/auth/plugins/api_key.py#L17-L35)
6. 卸载不是统一全量清理：`--uninstall` 直接处理的只有 Cursor、TRAE、TRAE CN、ZCode、Kimi Code、CodeBuddy Code 和旧 TraeCode CLI 配置；其他 harness 需按安装结束输出的命令手工卸载，凭据和服务器数据不由该流程清除。[卸载实现 L2656-L2798](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2656-L2798)、[主流程提前退出 L3814-L3818](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3814-L3818)

## 1. 实际下载/跳转链路与固定指纹

### 1.1 本次实际响应

本次 `GET https://openviking.ai/install` 直接返回 **HTTP/2 200**，`Content-Type: text/plain; charset=utf-8`，没有 3xx 跳转；`Content-Length` 与实际文件均为 5297 字节。`-L` 是为可能存在的跳转准备，并不代表本次发生了跳转。动态 URL：[bootstrap](https://openviking.ai/install)。

bootstrap 若被执行，会在临时目录中并行下载以下两个 URL，使用首个下载成功且第一行为 shebang、前五行含 `OpenViking Memory Plugin shared installer` 的副本，再把选中的 download base 导出给后续安装步骤：[固定 bootstrap L59-L100](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/bootstrap.sh#L59-L100)、[执行与清理 L102-L122](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/bootstrap.sh#L102-L122)。

- `https://docs.openviking.net/dl/memory-plugin-shared/install.sh`
- `https://docs.openviking.ai/dl/memory-plugin-shared/install.sh`

本次两域下载结果**逐字节相同**；`.ai` 的 HEAD 也直接返回 HTTP/2 200、无跳转。随后实际调用为 `bash <tmp>/install.sh --dist tos [原参数]`，输入优先接 `/dev/tty`，否则 `/dev/null`，EXIT trap 删除临时目录。这里的标题/shebang 检查不是签名或哈希校验。

### 1.2 研究对象指纹

| 对象 | 版本/固定点 | 字节数 | SHA-256 |
|---|---|---:|---|
| 线上 `openviking.ai/install` | `bootstrap-version: 1` | 5297 | `951b42cf83f83c5912c46609324fba1b2d2d526a67ca6a1779c4628061ca958e` |
| 线上 shared installer（`.ai` 与 `.net` 相同） | `INSTALLER_VERSION="20261101-6b2bcf29b2"` | 171453 | `900b5e977c481d50bd6d82fe37b9781f130466530a00e10abfb6363982b23f31` |
| 构建 commit 的 `bootstrap.sh` | [`6b2bcf29…`](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/bootstrap.sh) | 5040 | `3301c257b94464741e02c4e03d0a193e57b857defdd668b70aa03117d086a390` |
| 构建 commit 的 `install.sh` | [`6b2bcf29…`](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh) | 171437 | `0d3a2298e3fcffa394c69aabea7a6cd60e56a39c750146d54869d6b31b43bf04` |
| 当前官方 main（本次 `git ls-remote`） | [`4b92b30656c821712e2cf5f053963dedc66206d9`](https://github.com/volcengine/OpenViking/commit/4b92b30656c821712e2cf5f053963dedc66206d9) | 与构建 commit 两文件分别相同 | 与构建 commit 两文件分别相同 |
| v0.5.2.1 `bootstrap.sh` | [`f5e6203033a1…`](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/bootstrap.sh) | 5138 | `82cf620ab8366364ed64d02e82dd05b35ee9adfcc8342a7e4183e4aa853d124b` |
| v0.5.2.1 `install.sh` | [`f5e6203033a1…`](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/install.sh) | 146575 | `6b6c5acdeb6e25d6d63366de3e7d867664b96bca3a5820beaa6c3b802a986edb` |
| 当前 `releases/latest/memory-plugin-marketplace.zip` | 动态 release 包，仅作本次研究指纹 | 1483107 | `d571ab3657137fb1ac6a1197abae015e1efb50b23e9733212a430ba15fc8c30d` |

**来源对应关系**：线上 shared installer 与构建 commit 的 `install.sh` 比较，唯一差异是 `INSTALLER_VERSION="dev"` 被替换为 `20261101-6b2bcf29b2`，与脚本自己声明的发布规则一致。[版本行及发布规则 L93-L106](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L93-L106)

线上 bootstrap 与构建 commit/main 相比，多了一段五行 `For AI agents` 注释；与 v0.5.2.1 相比则只多两行 onboarding 文档注释。两种比较均未发现可执行语句差异。线上发布副本为何保留这段 main 已删除的注释，本次没有从官方资料确认，不作原因推断。

### 1.3 后续版本解析和下载

确认安装后，archive 模式会对每个选中 harness（`cli` 除外；`dsh-desktop` 映射为 `dsh`）请求：

```text
https://openviking.ai/install/v1/<harness>.json
```

请求带 `openviking-installer/<version> (<os>; <arch>)` User-Agent；响应可把 release、bundle URL、SHA-256、Codex git URL 固定到某个版本，URL 必须落在配置的 download bases 内。失败或无效响应会静默回退到 `releases/latest`。[解析实现 L1844-L1920](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1844-L1920)

本次以线上安装器的 Linux/x86_64 User-Agent 静态请求这些端点：

- `claude`、`codex`、`cursor`、`trae`、`trae-cn`、`trae-cli`、`zcode`、`kimicode`、`opencode`、`pi`、`dsh` 均返回 `version: 20261101-6b2bcf29b2`；文件型 harness 指向 `releases/latest/memory-plugin-marketplace.zip`，Codex 指向 `plugins/memory-plugins.git`。示例：[claude 响应](https://openviking.ai/install/v1/claude.json)、[codex 响应](https://openviking.ai/install/v1/codex.json)。
- 本次 `codebuddy.json` 返回 **404**；按上述源码会回退 latest，而不是中止。[codebuddy 动态端点](https://openviking.ai/install/v1/codebuddy.json)
- 本次成功响应未含 `sha256`，所以标准流程没有从 resolver 获得插件包校验值；上表插件包 SHA-256 是研究时独立计算的指纹，**不是官方 resolver 下发的校验值**。安装器只在取得非空校验值时验证；自定义 `OPENVIKING_MARKETPLACE_ARCHIVE_URL` 也明确不校验。[下载校验 L1723-L1743](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1723-L1743)、[bundle 选择 L1955-L1974](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1955-L1974)

这个 `20261101-…` 是安装器/插件发布标识，**不是服务器语义版本**。`ov` CLI 和 dsh 插件仍分别使用 npm 的 `@openviking/cli@latest`、`@openviking/dsh-memory-plugin@latest`。[dsh L1276-L1300](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1276-L1300)、[CLI L3425-L3440](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3425-L3440)

### 1.4 与 v0.5.2.1 的关键差异

当前线上 shared installer 相对 v0.5.2.1 标签为 **515 行新增、68 行删除**（本地下载固定文件后的 `git diff --no-index --stat`；构建 commit/main 的 dev 源码对照则为 514/67，差额就是发布版本行）。关键变化包括：

- 新增 **CodeBuddy Code** 和 **DeepSeek Harness Desktop (`dsh-desktop`)**；v0.5.2.1 的支持列表没有这两项。[v0.5.2.1 参数表 L222-L247](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/install.sh#L222-L247)、[当前参数表 L237-L264](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L237-L264)
- 新增 `NOT_DONE` 汇总，许多跳过/失败项会在结尾再次列出；dsh 增加 Desktop profile、运行中检测、当前 session profile 继承和 dev 包内容指纹等处理。[当前 L152-L167、L1242-L1452](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1242-L1452)
- 增加 CodeBuddy/Desktop 校验及按下载域选择 onboarding 指南。[当前验证 L3268-L3293、L3362-L3379](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3268-L3293)、[onboarding L3671-L3683](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3671-L3683)

因此不能用 v0.5.2.1 标签里的安装器代替对当前线上安装器的判断；但服务器认证结论仍应匹配实际服务器版本。

## 2. `curl -fsSL` 与向 bash 传参

| 参数 | 官方含义 |
|---|---|
| `-f`, `--fail` | HTTP 400 及以上通常以错误码 22 失败且不输出响应正文；并非所有 HTTP 错误情形都绝对可靠。[curl 手册](https://curl.se/docs/manpage.html#-f) |
| `-s`, `--silent` | 静默进度、提示、警告和错误；**仍输出请求正文**。[curl 手册](https://curl.se/docs/manpage.html#-s) |
| `-S`, `--show-error` | 与 `-s` 合用时，失败仍显示错误。[curl 手册](https://curl.se/docs/manpage.html#-S) |
| `-L`, `--location` | 遇到 3xx `Location` 时重新请求新地址。[curl 手册](https://curl.se/docs/manpage.html#-L) |

以下命令只输出脚本：

```bash
curl -fsSL https://openviking.ai/install
```

未给 `-o`/`--output` 或 shell 重定向时，正文到 stdout；在终端通常就是显示文本。以下形式才把 stdout 作为 bash 的输入并执行：

```bash
curl -fsSL https://openviking.ai/install | bash
```

官方给出的非交互完整形式是：[bootstrap 固定源码 L17-L21](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/examples/memory-plugin-shared/bootstrap.sh#L17-L21)、[shared installer L20-L28](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L20-L28)

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --yes --url <server-url> --api-key <key>
```

其中 `bash -s --` 后面的 token 才是交给脚本的参数，例如 `... | bash -s -- --harness claude --yes`；不要把安装器参数直接写成 bash 自身选项。bootstrap 最终把参数原样转交 shared installer，并在前面补 `--dist tos`。[参数解析 L287-L308](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L287-L308)

## 3. 参数、默认值、交互与预检

### 3.1 完整参数表

主依据：[usage L237-L285](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L237-L285)、[parser L287-L308](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L287-L308)。未知参数打印 usage 并以 2 退出。

| 参数 | 默认/条件与实际行为 |
|---|---|
| `--harness LIST` | 逗号分隔。可选 `claude,codex,cursor,trae,trae-cn,trae-cli,zcode,kimicode,codebuddy,opencode,pi,dsh,dsh-desktop,cli`。默认检测到的 agents + `cli`；一个也未检测到时为 `claude,codex,cli`。显式给 LIST 后不会自动附加 `cli`。[选择逻辑 L1208-L1240](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1208-L1240) |
| `--claude-bin LIST`（别名 `--claude-bins`） | Claude 格式命令，默认 `claude`；可指定多个兼容 CLI。 |
| `--codex-bin LIST`（别名 `--codex-bins`） | Codex 格式命令，默认 `codex`。`trae-cli` 安装会规范化到 Codex 路径和 `trae-cli`/`traecli`/`traex` 命令。[L587-L630](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L587-L630) |
| `--dsh-profile NAME` | 默认 `web`；在 dsh 启动的 shell 中可继承当前 session 的 `DSH_PROFILE`。`desktop` 会转去 `dsh-desktop`。[L1257-L1274](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1257-L1274) |
| `--lang en|zh` | 未指定时先按 `OPENVIKING_LANG`/`LC_ALL`/`LANG` 推断；交互模式可再问一次，非交互用推断值。非法值退出 2。[L410-L439](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L410-L439) |
| `--source archive|dev` | 经 bootstrap/管道安装为 `archive`。直接从 checkout 运行、未强制 `--dist tos` 且未自定义 archive URL 时默认 `dev`，使用 checkout 的插件；`archive` 使用发布包。旧 `remote` 作为 `archive` 别名接受并警告。[L1922-L1947](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1922-L1947) |
| `--dist CHANNEL` | 当前无渠道效果；接受 `tos`。旧 `github` 仅警告已移除，其他值报错。bootstrap 总是传 `--dist tos`。 |
| `--url URL` | 可选。未给时沿用已有 `ovcli.conf.url`，再没有则 `http://127.0.0.1:1933`。 |
| `--api-key KEY` | 可选。未给时沿用已有 key，否则空 key；`--api-key ''` 显式配置无鉴权本地连接。交互输入使用无回显读取。 |
| `--account ID` | 可选；未给保留已有值，显式空值会删除配置中的 `account`。API Key 模式下身份实际由 key 推导，见 §7。 |
| `--user ID` | 可选；保留/删除规则同 `account`。 |
| `--statusline` | 不询问，注册原生 Claude Code 的 OpenViking statusline。 |
| `--no-statusline` | 不询问并跳过 statusline。默认只对可安装插件的原生 `claude` 询问，菜单默认跳过；无 TTY 时不新增，除非显式 `--statusline` 或升级安装器自己旧 statusline。[L2171-L2209](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2171-L2209) |
| `--uninstall` | 只走 §5.4 所述部分卸载路径；在凭据、计划和安装确认之前结束。 |
| `--yes`, `-y` | 禁止所有询问，打印计划后继续；不是 dry-run。 |
| `--help`, `-h` | 打印 usage 并退出。经管道使用时仍已下载并执行 bootstrap/shared 的解析部分；纯审查优先直接保存文本阅读。 |

`--account`/`--user` 没有交互 prompt；连接向导只处理 URL 和 key。已有凭据时可选择沿用或重配；新配置可选 local、Volcengine Cloud 或 custom URL，key 用 `read -rs` 从 `/dev/tty` 读取。[连接向导 L1541-L1586](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1541-L1586)

### 3.2 交互顺序与确认边界

典型交互顺序为：语言 → agents/兼容 CLI → dsh profile → URL/key → 服务探测（失败可重输或继续）→ 可选 Claude statusline → 计划确认 → release 解析与写入。[主流程 L3784-L3862](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3784-L3862)

“确认前不写配置”准确地说是：确认前不写 `$OV_HOME` 或 harness 配置；但 bootstrap 已下载脚本，安装器已做本机检测、host CLI 查询和服务器探测，并创建临时文件。交互取消后退出；非交互 `tui_menu` 直接选默认“继续”。[L3829-L3832](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3829-L3832)

`--uninstall` 是例外：选择 harness 后直接执行部分卸载，不经过正常安装计划确认；与 `--yes` 合用时应精确限定 `--harness`。[L3808-L3818](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3808-L3818)

### 3.3 平台、架构、依赖和检测

- **OS**：只允许 `uname -s` 为 `Darwin` 或 `Linux`；其他 OS 退出。`dsh-desktop` 仅 macOS。没有 `uname -m` allowlist，架构只放入版本解析 User-Agent；因此安装器本身不证明 npm 平台二进制或各 host 支持当前架构。[OS/Node 预检 L3790-L3806](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3790-L3806)、[版本解析 UA L1874-L1885](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1874-L1885)
- **Bash**：头部声明 3.2+，但没有单独的 Bash 版本预检。[头部 L28-L31](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L28-L31)
- **Node.js**：所有路径强制 18+；pi 另强制 **22.19.0+**。[pi L3108-L3118](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3108-L3118)
- **curl**：缺失只警告并跳过 server check，后续需要下载的步骤仍会失败。
- **unzip**：只有实际解 release archive 时强制；纯 CLI 或不需要 bundle 的路径可能不需要。[L1723-L1727](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1723-L1727)
- **npm**：`cli` 需要，缺失则跳过 CLI；pi 需要 `npm ci`，缺失是错误；dsh 需要已有 npm 版 `dsh` 命令，dev 打包另需 npm。[CLI L3404-L3440](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3404-L3440)、[dsh L1303-L1320](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1303-L1320)
- **Claude Code**：需要带 `plugin` 子命令（脚本提示 2.0+）；原生 Claude 的 URL archive marketplace 要求版本至少 `2.1.224`，否则回退目录 marketplace。[L118-L120](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L118-L120)、[L2091-L2115、L2295-L2324](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2091-L2115)

agent 自动检测依据包括 PATH 中的 `claude/codex/cursor/cursor-agent/trae-cli/traecli/traex/opencode/pi/dsh/zcode/kimi/codebuddy`，以及若干 macOS app 或 `~/.cursor`、`~/.trae`、`~/.trae-cn`、`~/.zcode`、`~/.kimi-code`、`~/.codebuddy` 等目录。[检测实现 L569-L584](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L569-L584)

统一安装器列表不等于全部 OpenViking 官方集成：OpenClaw、Hermes、Agent Plugins 1.0 等在官方文档中有其他接入方式，但不是当前 `--harness` 的可选值。[官方集成总览 L5-L24](https://github.com/volcengine/OpenViking/blob/4b92b30656c821712e2cf5f053963dedc66206d9/docs/en/agent-integrations/01-overview.md#L5-L24)

## 4. 环境变量、配置文件、目录与权限

### 4.1 安装器实际读取的环境变量

官方 help 列出主要变量：[L265-L284](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L265-L284)。源码还包括以下兼容/路径变量：[初始化 L103-L179](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L103-L179)。

| 类别 | 变量与作用 |
|---|---|
| 基础路径 | `OPENVIKING_HOME`（默认 `~/.openviking`）；`OPENVIKING_CLI_CONFIG_FILE`（默认 `$OPENVIKING_HOME/ovcli.conf`）；`OPENVIKING_MARKETPLACE_NAME`（默认 `openviking`）。 |
| 语言/命令选择 | `OPENVIKING_LANG`；`OPENVIKING_CLAUDE_BINS`（兼容旧 `OPENVIKING_CLAUDE_BIN`）；`OPENVIKING_CODEX_BINS`（兼容旧单数）；`OPENVIKING_DSH_PROFILE`。 |
| 下载/发布 | `OPENVIKING_DOWNLOAD_BASE`（旧名 `OPENVIKING_TOS_BASE`）；`OPENVIKING_MARKETPLACE_ARCHIVE_URL`；`OPENVIKING_CLAUDE_MARKETPLACE_URL`；`OPENVIKING_CODEX_TOS_GIT_URL`；`OPENVIKING_INSTALL_SITE`；`OPENVIKING_SKIP_VERSION_CHECK`（除 `0`/空外均跳过）；`OPENVIKING_INSTALLER_REEXEC=0` 防止非发布副本重下发布安装器。 |
| host 路径 | `CODEX_HOME`、`CODEX_CONFIG_FILE`、`CLAUDE_CONFIG_DIR`、`TRAE_HOME`、`TRAECLI_HOME`、`KIMI_CODE_HOME`、`DSH_HOME`、`OPENVIKING_DSH_DESKTOP_CLI`。 |
| dsh session | `DSH_SHELL=1` 时读取当前 `DSH_PROFILE` 作为安装默认 profile。[L153-L157](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L153-L157) |
| 旧变量 | `OPENVIKING_REPO_URL`、`OPENVIKING_REPO_REF`、`OPENVIKING_REPO_BRANCH` 只属于已移除 GitHub channel，出现即警告并忽略。[L1934-L1946](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1934-L1946) |

`OPENVIKING_STATUSLINE=off` 是安装结束后提示用户供 statusline 运行时使用的变量，不是安装器选项；`OPENVIKING_MEMORY_ENABLED=0` 是校验时主动设置以禁用记忆副作用。[statusline L2211-L2227](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2211-L2227)、[校验 L3189-L3194](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3189-L3194)

**当前安装器没有 `OPENVIKING_URL`、`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER` 输入参数。**这些连接值来自命令行或已有 `ovcli.conf`；不要把其他插件运行时的环境变量支持误当成统一安装器接口。

### 4.2 共用文件与权限

| 文件/目录 | 新建、合并、覆盖与备份 | 权限 |
|---|---|---|
| `$OPENVIKING_HOME` | 安装写入前创建。 | 尝试 `chmod 700`。[L1703-L1709](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1703-L1709) |
| `$OPENVIKING_CLI_CONFIG_FILE` | 每次**确认后的安装运行**都写；有效 JSON 合并 `url/api_key/account/user`，保留其他字段；未指定的 key/account/user 保留。旧文件复制为 `ovcli.conf.bak.<epoch>`。无效 JSON 会被当作空对象，但旧文件先备份。[L677-L699](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L677-L699) | 主文件写入后 `chmod 600`；备份只 `cp`，没有单独 chmod 声明。[L1703-L1717](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1703-L1717) |
| `$OPENVIKING_HOME/memory-plugin-marketplace` | 下载 zip 后替换；已有目录若无 `.openviking-archive-source` marker 则拒绝覆盖。[L1752-L1765](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1752-L1765) | 未单独声明。 |
| `$OPENVIKING_HOME/agent-integrations` | Cursor/TRAE/ZCode 等运行目录按管理边界替换；保留首次安装时间等 manifest 信息。[L2545-L2613](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2545-L2613) | 各 JSON 原子写入见下文。 |
| `~/.openviking/ovcli.settings.conf` | CLI 安装后若不存在，执行 `ov language` 初始化显示语言；注意此路径固定使用 `$HOME/.openviking`，不随自定义 `OPENVIKING_HOME` 改写。[L132-L135、L3442-L3448](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3442-L3448) | 由 `ov` 决定。 |

### 4.3 各 harness 的写入边界

总计划列出各 harness 路径：[L3512-L3627](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3512-L3627)。下表补充“合并还是覆盖”。

| harness | 主要写入与合并行为 |
|---|---|
| `claude` | 调用 `claude plugin marketplace add/update`、`plugin install/update/enable`。`$CLAUDE_CONFIG_DIR/settings.json`（默认 `~/.claude/settings.json`）合并 marketplace `autoUpdate`；可选写入 `statusLine`。statusline 写入前保存 `settings.json.bak.<timestamp>`（`cp -p`），autoUpdate 本身没有独立备份。会清理可识别的旧 OpenViking marketplace/hooks/MCP。[L2067-L2133](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2067-L2133)、[L2211-L2279](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2211-L2279) |
| `codex` / `trae-cli` | 由 host CLI 管理 marketplace/plugin。`$CODEX_CONFIG_FILE`（默认 `~/.codex/config.toml`）新建或文本合并：启用对应 plugin 和 `features.plugin_hooks`；无备份、无显式 mode。TraeCode 成功启用新插件后清理旧 `~/.trae/cli/hooks.json`、`~/.trae/traecli.toml` 条目。[L2459-L2536](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2459-L2536) |
| `cursor` | 合并 `~/.cursor/hooks.json`、`~/.cursor/mcp.json` 中 OpenViking 自有条目，保留其他条目；替换 rule `~/.cursor/rules/openviking-memory.mdc` 和三个 skills 目录。[L2831-L2856](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2831-L2856) |
| `trae` / `trae-cn` | 合并各自 `hooks.json` 与 MCP 配置。Linux 下为 `~/.trae/mcp.json` 或 `~/.trae-cn/mcp.json`；macOS 下为 `~/Library/Application Support/Trae/User/mcp.json` 或 `Trae CN/User/mcp.json`。[L2816-L2829、L3023-L3033](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2816-L2829) |
| `zcode` | 先生成 `~/.zcode/hooks.json`、`~/.zcode/mcp.json`，再合并进实际读取的 `~/.zcode/cli/config.json`；已存在且不属于 OpenViking 的同名 MCP server 会跳过。[L2862-L2884](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2862-L2884)、[helper L253-L285](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/lib/install/host-json-config.mjs#L253-L285) |
| `kimicode` | 在 `$KIMI_CODE_HOME/plugins` 下更新 `installed.json` 和 `managed/openviking-memory`；staging 替换失败时回滚，成功后旧目录删除，不保留长期备份。[L2886-L2942](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2886-L2942)、[helper L31-L80](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/lib/install/kimicode-plugin.mjs#L31-L80) |
| `codebuddy` | 在 `$OPENVIKING_HOME/agent-integrations/codebuddy/marketplace` 组装并替换本地 marketplace，再调用 `codebuddy plugin marketplace add` 与 `plugin install`；host 管理 `~/.codebuddy/plugins` 缓存。[L2944-L3021](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L2944-L3021) |
| `opencode` | 替换 `~/.config/opencode/plugins/openviking` 和 `openviking.js`；合并 `opencode.json`/`.jsonc` 的 plugin/MCP 配置并保留 JSONC 注释。写配置前复制 `.bak.<timestamp>`；已有 `mcp.openviking.enabled=false` 时不强行启用。[L3039-L3091](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3039-L3091)、[JSONC helper L318-L356](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/lib/install/jsonc-edit.mjs#L318-L356) |
| `pi` | staging 中 `npm ci` 成功后替换 `~/.pi/agent/extensions/openviking`；并调用 `pi remove <dest>` 清除旧 packages 注册，防止与 auto-discovery 重复加载。[L3108-L3155](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3108-L3155) |
| `dsh` / `dsh-desktop` | 调用相应 `dsh plugin --profile <name> add @openviking/dsh-memory-plugin@latest`；Desktop 固定 `desktop` profile，配置由其 app 内 CLI 管理。[L1303-L1393](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1303-L1393) |
| `cli` | `npm install -g @openviking/cli@latest --ignore-scripts --no-audit --no-fund`。npm 全局 prefix 可写就用该 prefix，否则用 `~/.local`，目标为 `<prefix>/bin/ov`。[L3404-L3449](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3404-L3449) |

Cursor/TRAE/ZCode 的 JSON helper 对不可解析配置会报错而不是覆盖；写入采用临时文件 + rename，旧非空内容保存为单个 `.bak`。新配置和 `.bak` 均以 mode `0600` 创建；安装只替换自己拥有的 hooks/MCP 条目。[helper L21-L58、L147-L212](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/lib/install/host-json-config.mjs#L21-L58)。其他 host 配置除上述明确项外没有统一 `0600` 保证，多由现有文件、umask 或 host CLI 决定。

**PATH/shell 配置**：所审安装器不写 `~/.bashrc`、`~/.zshrc` 等 shell rc，也不自行把 CLI bin 加入 PATH；只刷新当前 bash 子进程的命令 hash，并警告 PATH 缺失或存在另一个 `ov`。[L3449-L3455](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3449-L3455)

## 5. 网络、进程、hooks/MCP/skills、重跑与卸载

### 5.1 网络和包管理器

正常 archive 安装可能联系：

1. `openviking.ai`：bootstrap 和每 harness 版本解析；`OPENVIKING_INSTALL_SITE` 可改。
2. `docs.openviking.net` / `docs.openviking.ai`：shared installer、marketplace 小文件、插件 zip、Claude URL marketplace、Codex dumb-HTTP git marketplace。选择任一最快可用域后，后续使用同一 base。[L1781-L1813](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1781-L1813)
3. 用户给定 OpenViking Server：`GET /health` 与 `GET /api/v1/system/status`。
4. 本机 npm 配置的 registry：CLI 及平台 optional dependency、pi dependencies、dsh package；脚本不硬编码 registry host。

脚本声明自身正常安装不访问 `github.com`、不直接运行 `git`；GitHub 只用于人工核对源码。Codex/Claude host CLI 自己可能拉取 marketplace 并执行后续自动更新。[头部网络说明 L33-L48](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L33-L48)

### 5.2 进程、服务和安装后自动行为

- 不调用 `sudo`，不安装/启动 OpenViking Server，不创建账号、用户或 key；也没有 systemd/udev/Docker 部署步骤。npm 全局 prefix 可写时可能写 `$HOME` 之外，临时文件也通常在 `/tmp`。
- 会执行本机 host CLI、Node helper、npm；还会对下载/组装的 JS 做 `node --check`、实际 module import 和 `OPENVIKING_MEMORY_ENABLED=0` 的 hook smoke test，并非纯文件复制。[OpenCode import L3051-L3054](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3051-L3054)、[pi import L3132-L3142](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3132-L3142)、[hook 校验 L3164-L3194](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3164-L3194)
- Claude/Codex/Cursor/TRAE/ZCode/Kimi/CodeBuddy/OpenCode/pi/dsh 会获得各自 hooks 或生命周期扩展及 stdio MCP proxy；Cursor 另安装 rule 和三个 skills，CodeBuddy 包也含 skills。多数要求重启或新会话才加载。[各 harness 后续动作 L3685-L3767](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3685-L3767)
- 原生 Claude URL marketplace 会设 `autoUpdate: true`，Claude 后台检查更新；Codex git marketplace 可在启动时升级。即“不重跑安装器也可能更新插件”。[L2117-L2133、L3693-L3725](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3693-L3725)
- dsh Desktop 只用 `pgrep`/`osascript` 检测运行状态；交互时要求用户按 ⌘Q，脚本不杀进程。非交互检测到运行时反而警告并继续，需用户之后重开。[L1322-L1393](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1322-L1393)

### 5.3 升级、重跑和幂等边界

重跑采用 marketplace/plugin update、受管目录替换、OpenViking 自有配置条目替换，通常可重复；已有用户条目大多保留，manifest 尽量保留首次安装时间。CLI/dsh 使用 `@latest`，release resolver 失败也回退 latest，因此重跑可能升级而非不变。

它不是严格事务或严格无操作幂等：

- 每次确认安装都会重新写 `ovcli.conf` 并创建时间戳备份，即使值未变。
- 多 harness 中部分失败常记录到 `NOT_DONE` 后继续，可能留下部分完成状态；没有全局回滚。[`NOT_DONE` L194-L199](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L194-L199)、[结尾汇总 L3768-L3777](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3768-L3777)
- 不升级或修复 OpenViking Server，也不保证插件 release 与服务器版本兼容。

### 5.4 卸载和清理

官方直接卸载命令形式：[头部 L88-L91](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L88-L91)

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness <list>
```

实际边界：

- **直接处理**：`cursor,trae,trae-cn,zcode,kimicode,codebuddy`，以及旧 `trae-cli` hooks/MCP；共享 runtime 在不再被其他集成使用时删除。
- **仅给手工命令**：Claude、Codex/TraeCode、OpenCode、pi、dsh/dsh-desktop 和 CLI 的卸载命令在正常安装结束输出；不要把 `--uninstall --harness claude` 当成已完整卸载。[L3693-L3767](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3693-L3767)
- JSON hooks/MCP 卸载只移除自己拥有的条目，并删除这些配置对应的 `.bak`；不清理无关用户配置。[helper L215-L243](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/lib/install/host-json-config.mjs#L215-L243)
- 卸载路径在 `gather_credentials`/`write_ovcli` 前退出，所以**不删除或重写 `ovcli.conf`**；也没有统一删除所有备份、marketplace archive、CLI settings、Server 或其 tenant 数据的官方全量清理命令。[主流程 L3814-L3832](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3814-L3832)

因此官方有部分卸载能力，但**没有单一、完整、覆盖所有 harness 和服务器数据的卸载器**。

## 6. 典型用法与 key 输入安全

以下均为说明示例，本次研究未执行任何安装示例；尖括号均为占位符，不是凭据。

### 6.1 只审查，不执行

直接查看 stdout：

```bash
curl -fsSL https://openviking.ai/install
```

保存到独立目录后审查和固定指纹：

```bash
review_dir=$(mktemp -d)
curl -fsSL https://openviking.ai/install -o "$review_dir/bootstrap.sh"
wc -c "$review_dir/bootstrap.sh"
sha256sum "$review_dir/bootstrap.sh"
less "$review_dir/bootstrap.sh"
```

如需审查 shared installer，应再用另一个新空目录下载其 URL；不要运行 `bash`、`sh`、`source` 或管道。

### 6.2 交互安装

```bash
curl -fsSL https://openviking.ai/install | bash
```

在 TTY 中可选择语言、agents、local/cloud/custom URL；key 无回显输入，最后确认计划。限定 agent 但仍保留交互和隐藏 key 输入：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness claude
```

### 6.3 非交互指定 URL/account/user 和 agents

官方参数形式：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- \
  --yes --lang zh \
  --harness claude,codex,cli \
  --url 'https://openviking.example.com' \
  --account '<account-id>' \
  --user '<user-id>' \
  --api-key '<user-or-admin-key>'
```

选择特定 agent 只需改 LIST，例如 `--harness cursor`；显式列表不含 `cli` 时不会自动安装 CLI。本地无鉴权模式可显式 `--api-key ''`，但不要把它用于实际需要认证的远程服务。

若已有正确的 `ovcli.conf`，非交互重跑可省略 `--api-key`，从而沿用现有 key；未提供 key 且没有已有配置时会回退为空 key，而不是安全地报错。[L1600-L1640](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1600-L1640)

### 6.4 命令行 key 的风险与官方替代输入

把真实 key 直接写入 `--api-key <key>` 会把它留在 shell history（若交互输入该命令）和相应进程的 argv/process list 中；管道场景还会经 bootstrap 传给 shared installer。安装器源码在后续 Node 调用处也明确指出了“参数可被其他用户从 process list 读取”这一风险，因此“命令在一次性脚本里”不等于没有留痕。

安装器内部有两项减 exposure 措施，但不能消除初始 argv 风险：

- 调 Node 合并配置时通过环境传 key，明确避免把 key 放进 Node process list。[L677-L697](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L677-L697)
- 健康探测用 `curl -K -` 从 stdin 传 Authorization header，避免把 key 放进 curl argv。[L1642-L1662](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1642-L1662)

官方更安全的安装器输入方式是**交互式无回显 prompt**，或预先已有权限受控的 `ovcli.conf` 后省略 key 参数；当前 parser 没有 `--api-key-stdin`、key fd 或安装器专用 `OPENVIKING_API_KEY` 输入。输出中的 key 只做掩码显示，仍应避免把安装日志当作可公开材料。[隐藏输入 L1563-L1584](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1563-L1584)

## 7. 健康探测和安装校验能证明什么

### 7.1 实际探测算法

1. 对 `<url>/health` 发不带 key 的 GET，5 秒超时，丢弃 body，只看 HTTP code；连接失败记 `unreachable`，否则继续。
2. 对 `<url>/api/v1/system/status` 发 GET，按需带 `Authorization: Bearer <key>`、`X-OpenViking-Account`、`X-OpenViking-User`，同样 5 秒超时并丢弃 body。
3. status 返回 2xx 记 `ok`；401/403 记 key 被拒绝或需要 key；其他 HTTP code 只警告。[L1650-L1669](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1650-L1669)

**失败不阻止安装**：交互时可重输或“仍然继续”；非交互记录 `NOT_DONE` 后返回，主流程仍打印计划、确认并写配置。[L1671-L1700](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L1671-L1700)

所以 `ok` 最多证明：当时网络可达、HTTP 服务返回响应、该 status endpoint 接受了请求上下文。它不检查 `/health` body，也不读取 status 返回的 `initialized`/`user` 字段。v0.5.2.1 的 status handler 只要求 `get_request_context`，没有 ADMIN role guard。[`system.py` L182-L199](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/routers/system.py#L182-L199)

### 7.2 USER / ADMIN / ROOT 的关键差异

| key | 能否可能通过安装器 status 探测 | 后续数据面/MCP |
|---|---|---|
| USER | 可以；status 不要求 ADMIN | 官方明确支持，受 account/user 与资源 ACL 约束 |
| ADMIN | 可以 | 可以，但以自己的 user 身份访问；**在 v0.5.2.1 上还可通过 `GET /api/v1/admin/accounts/<acct>/users`（默认 `include_credentials=true`）取得本账户所有 user 的明文 key，并可轮换任何 user 的 key**，效果上等价于账户内数据访问（见 [auth-roles 笔记 §2.2/§5.2](2026-11-02-openviking-agent-auth-roles.md)） |
| ROOT | **也可能通过**，因为 `/api/v1/system/status` 在 ROOT allowlist | API Key 模式下不能访问 tenant-scoped 数据 API；不应作为正常插件/MCP 数据 key |

官方两层 key 模型和 tenant 规则见 [Authentication L46-L55、L156-L165](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/04-authentication.md#L156-L165)；MCP 明确要求 User/Admin key，见 [MCP Authentication L28-L35](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/06-mcp-integration.md#L28-L35)。ROOT 的 allowlist 和 tenant 拒绝实现见 [`api_key.py` L17-L35、L203-L217](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/auth/plugins/api_key.py#L203-L217)。

另一个容易误判的点：API Key 模式下 `account`/`user` 由 key 推导，服务端会移除身份断言 headers；因此给 `--account`/`--user` 后探测成功，并不证明这两个参数与 key 实际绑定身份一致。[`api_key.py` L84-L96](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/openviking/server/auth/plugins/api_key.py#L84-L96)、[Authentication L108-L117](https://github.com/volcengine/OpenViking/blob/f5e6203033a13469c3d9a937f2ca39492ea69c51/docs/en/guides/04-authentication.md#L108-L117)

### 7.3 安装结束校验也不是端到端数据面验收

`validate_install` 主要检查：host plugin list、文件存在、JS 语法、部分 module import、配置条目存在、禁用记忆功能的 hook smoke，以及 dsh profile 组合。它没有执行真实 `find`/read/write/resource/session 流程，没有完成 MCP handshake/tools call，也不验证 embedding、VLM、reranker 或 `/ready`。[L3197-L3391](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3197-L3391)

安装结束只是建议用户另行运行 `ov health`、host doctor/skill，安装器自身没有把这些建议全部执行。[L3693-L3767](https://github.com/volcengine/OpenViking/blob/6b2bcf29b2f4169b519dd96015b50adb3dca08f2/examples/memory-plugin-shared/install.sh#L3693-L3767) 因此“Server check passed”与“插件已在列表”都不能替代 USER key 的真实数据面与 MCP 验收。角色管理边界另见本仓库 `docs/research/2026-11-02-openviking-agent-auth-roles.md`。

## 8. 未覆盖或不确定事项

1. 本次没有执行安装、卸载、`--help` 管道或任何 host CLI/npm 安装命令，因此交互 UI 的实际终端表现、各 host 版本兼容性和失败恢复未做动态验收。
2. 当前 release zip 已下载并计算 SHA-256，但未解包或审计其全部插件；配置 helper 的分析基于构建 commit 官方源码，未声称 zip 内每个文件都逐字节复核。
3. 未建立 npm optional platform binary 的 OS/CPU 支持矩阵；只能确认安装器没有架构 allowlist，不能据此保证所有 Linux/macOS 架构都能安装运行。
4. 版本 resolver、latest 包和动态脚本会变化；本笔记指纹只固定 **2026-11-02 本次访问**。codebuddy resolver 404 也是时点观察，不是永久结论。
5. 对 v0.5.2.1 → 当前线上安装器的 583 行增删差异只归纳关键功能差异，不是逐行安全审计；未覆盖所有 legacy 迁移分支和每个第三方 host 的内部实现。
6. 官方确实提供部分卸载路径，但没有官方统一全量卸载/服务器数据清理能力；本笔记不臆造额外清理命令。
