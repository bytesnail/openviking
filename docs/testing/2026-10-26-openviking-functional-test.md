# OpenViking v0.5.2 本机部署功能测试报告

- **日期**:2026-10-26
- **对象**:`/mnt/usr/tools/openviking` 本机 Docker Compose 部署
- **基线**:镜像 `ghcr.io/volcengine/openviking:v0.5.2@sha256:d1cc56a3…`(tag commit `7f3ae1af`);源码取自 GitHub tag `v0.5.2` 归档(测试期临时解压于 `/tmp`,已清理),行为论断对照 [源码核查笔记](../research/2026-10-25-openviking-source-verification.md) 与 [镜像实况核查](../research/2026-10-25-image-runtime-verification.md);判据依据 [部署设计 §9](../superpowers/specs/2026-10-25-openviking-compose-design.md)。
- **结论**:**全部通过,零部署级故障**;4 项运维级发现(F1–F4,均不阻塞日常使用,附缓解方法)。

## 1. 测试环境

| 项 | 值 |
|---|---|
| 容器 | `openviking-openviking-1`、`openviking-embedding-adapter-1`(均 healthy) |
| 上游模型 | `embeddinggemma-2`(127.0.0.1:18351)、`qwen3-vl-reranker-2b`(127.0.0.1:18352),model-serving 运行中 |
| VLM | Z.AI GLM Coding Plan `glm-5.3-flash`(真实调用) |
| 版本自证 | `/health`→`v0.5.2`、`ov --version`→`v0.5.2`、`ov health`→`auth_mode:api_key, role:admin` |

## 2. 结果矩阵

### 2.1 静态与单元(宿主机)

| 用例 | 结果 | 证据 |
|---|---|---|
| `docker compose config --quiet` | ✅ | CONFIG_OK |
| `python3 -m pytest tests/` | ✅ | 43 passed(26.5s,Python 3.14.7) |
| `ov config validate`(容器内) | ✅ | 配置文件有效/服务器可连接/认证通过/健康 |

### 2.2 运行态与边界

| 用例 | 结果 | 证据 |
|---|---|---|
| `curl /health`、`/ready` | ✅ | `healthy:true`;agfs/vectordb/api_key_manager/embedding 均 ok,ollama not_configured |
| `ov health -o json` | ✅ | `result.healthy:true`(嵌套形态与设计 §7.3 勘误一致) |
| 端口边界(`ss -tln`) | ✅ | 1933/18353/18351/18352 全部仅 `127.0.0.1`;18790 未启动 |
| Studio | ✅ | `GET /studio`→200;`GET /`→302 |

### 2.3 模型链路(真实调用)

| 用例 | 结果 | 证据 |
|---|---|---|
| `openviking-server doctor` | ✅ | 10/10 PASS;embedding 真实探针 dimension=768 |
| `smoke_models.py` | ✅ | vlm-text 答对、vlm-image 识红、rerank 0.702>0.376 排序正确 |

### 2.4 适配器实况(`tests/e2e_adapter_probe.py`,6/6)

| 用例 | 结果 | 证据 |
|---|---|---|
| 纯文本适配器 vs 直连上游 | ✅ | cosine=1.000000,双方 768 维 |
| OpenViking 裸 parts 多模态 | ✅ | HTTP 200,恰好 1 个 768 维向量 |
| 红蓝视觉语义 | ✅ | cosine_red=0.7484 > cosine_blue=0.6748 |
| 不支持 part 类型 | ✅ | HTTP 400 |
| `/health`、`/ready` | ✅ | 均 200 |

### 2.5 认证与安全(`tests/e2e_auth_matrix.sh`,8/8)

| 用例 | 结果 |
|---|---|
| 无 key / 错 key → tenant API | ✅ 401 |
| default ADMIN key(X-API-Key 与 Bearer)→ tenant API | ✅ 200 |
| Root key → tenant API | ✅ 403(与源码论断 24 一致:ROOT 不绑租户) |
| Root key → `/api/v1/admin/accounts` | ✅ 200 |
| default ADMIN key → admin API | ✅ 403(该端点是 ROOT-only) |
| 文件权限 | ✅ `state`/`state/secrets` 700,`user_api_key`/`ovcli.conf`(.local)/`secrets.env` 600 |
| 容器加固 | ✅ 适配器 readonly rootfs + cap_drop ALL + no-new-privileges + uid 65532;主服务 uid 1000 |

> 2026-11-02 复核:该脚本的 `ADMIN_KEY`(原 `USER_KEY`)来自 `state/secrets/user_api_key`,对应用户 `default` 的 **role=admin**(不是 role=user);上表原来写的 "User key" 实为 ADMIN key。它验证的是 ADMIN key 对 ROOT-only 端点的拒绝,不能证明普通 USER 边界。三身份方案创建 `test`(role=user)后应补测 USER 边界。另:`state/data/viking/default/_system/users.json` 保存 user key(当前 644,外层 `state` 700),是本文凭据清单未列的服务端副本,随 `state/` 进入备份。

### 2.6 端到端资源生命周期

| 用例 | 结果 | 证据 |
|---|---|---|
| `add-resource` 文本(`--to` 独立 URI,`--wait`) | ✅ | success;真实 LLM 4393/4727 tokens(两次),Semantic/Embedding 队列零错误 |
| `add-resource` 图片 ×2 | ✅ | 服务端任务 completed(64–66s);客户端超时见 F1 |
| `ov abstract` / `stat` | ✅ | 摘要准确提取独特事实"校准码 MX-0417";元数据完整 |
| `ov find` 文本 | ✅ | 独特事实资源霸榜前 4(0.884),泛化噪声 ≤0.869/0.813 |
| `ov find --image` 红图 | ✅ | red 资源 #1/#2(0.940/0.924)> blue(0.840) |
| `ov find --image` 蓝图(交叉) | ✅ | blue #1(0.8926);纯色图间边际小但 top-1 正确 |
| `ov read` / `ov get` | ✅ | 读回一致;下载 md5 与夹具相同(`8ac657e2…`) |
| `ov grep` / `glob` / `tree` | ✅ | "MX-0417" 精确定位 2 处;png glob 命中 3;树含摘要 |
| `ov write` → 读回 | ✅ | 内容逐字一致;触发异步语义任务 |
| `ov cp` / `ov mv` | ✅ | 首次因 F2 锁冲突失败,队列排空后重试成功 |
| `ov export` / `import`(ovpack) | ✅ | 导出-导入往返,3 文件与内容完整 |
| `ov task list` / 异步等待 | ✅ | 全部任务 completed;可靠姿势为轮询(见 F1) |
| 重启持久化 | ✅ | `docker compose restart` 后 Healthy,8 项资源在位,find 正常 |

## 3. 发现(F1–F4)

### F1|CLI 60s 默认超时 vs 图片语义任务时长(影响体验,非故障)

- **现象**:图片 `add-resource --wait` 与 `ov wait` 均报 `DEADLINE_EXCEEDED: operation timed out`(POST `/api/v1/resources` 或 `/api/v1/system/wait`),但服务端任务实际 **success**(任务耗时 64–66s)。
- **根因**:Rust CLI 配置默认 `timeout: 60.0` 秒(`crates/ov_cli/src/config.rs` `default_timeout`,序列化名 `timeout`),短于图片资源语义任务实测时长;文本任务 45–52s 未触发。
- **缓解(2026-10-26 已实施并验证)**:`state/ovcli.conf` 与 `state/ovcli.conf.local` 顶层已写入 `"timeout": 300`(改文件是唯一途径——`ov config edit`/`add` 均无 timeout 旗标);`ov config validate` 通过,`ov config show` 确认 CLI 实际加载 `timeout: 300.0`,图片 `add-resource --wait` 实测完整返回 success(51s,验证资源已删)。备选姿势:不带 `--wait` 提交 + 轮询 `ov task list -o json`。

### F2|同目录变更的锁竞争(0ms 即拒,重试即愈)

- **现象**:write 的异步任务在途时,紧随的 `cp` 报 `[CONFLICT] lock acquire timed out after 0ms`;日志见 `LockAcquisitionError` 被映射为结构化错误。add 场景亦复现一次(两次连续向同一 `--to` 提交)。
- **定性**:v0.5.2 目录级变更锁不排队、0ms 超时快速失败;等在途任务排空后重试即成功。单用户顺序操作 + 变更间轮询 `ov task list` 即可规避。

### F3|`--to` 指向已存在 URI 是"替换"语义(易误用)

- **现象**:三次 `add-resource --to viking://resources/e2e-20261026`(同一 URI)后目录里只剩最后一次的文件——前两次内容被更新替换。
- **依据**:CLI help 明示 `--to` "must not exist yet";服务端按 update_plan/diff_and_compile 处理已存在目标。多资源导入应每资源独立 `--to`(README 验收的用法)或先 `mkdir` 再用 `--parent`。

### F4|`state/ovcli.settings.conf` 权限 644(轻微)

- 仅含 CLI 语言设置,非凭据;README 凭据清单未列它,如求一致可 `chmod 600`。其余敏感文件权限全部达标。

## 4. 资源消耗(真实 GLM 调用)

- `smoke_models.py`:2 次(文本 + 图像)。
- 资源语义理解:4 个 add 任务(2 文本 + 2 图片)+ 1 次 write 任务;有遥测的 LLM 用量为 4393 与 4727 tokens(两次文本 add,含 reasoning,第二次 prompt_cached=384),图片/写任务明细在任务 JSON 截断部分未逐项记录。
- 嵌入与 rerank 全部走本机 model-serving,无外部消耗。

## 5. 清理记录

| 动作 | 结果 |
|---|---|
| 删除 6 个测试资源树(`deployment-smoke`、`deployment-smoke-image`、`e2e-20261026`、`-doc`、`-red`、`-import`) | ✅ `ov ls viking://resources` 为空 |
| 向量索引账目 | ✅ vector count 30→24,差额 6 恰为已删冒烟资源;余 24 属系统内容 |
| 容器 `/tmp`(下载件、ovpack)与宿主 `/tmp`(探针草稿、v0.5.2 源码归档) | ✅ 已删 |
| 仓库 `__pycache__`、`.pytest_cache` | ✅ 已删(gitignore 项,可再生) |
| 沉淀保留 | `tests/e2e_adapter_probe.py`、`tests/e2e_auth_matrix.sh`(见 §6) |

`deployment-smoke` 两目录原为"保留供检查"(README);本轮全面测试已完成检查职责,故删除,可按 README §验收 命令一键重建。

## 6. 复测方法(沉淀)

```bash
cd /mnt/usr/tools/openviking
python3 -m pytest tests/ -q                        # 单元:适配器协议 + 认证初始化(无外部依赖)
python3 tests/e2e_adapter_probe.py                 # 适配器实况 7 项(需服务在位,不耗 GLM)
bash tests/e2e_auth_matrix.sh                      # 认证边界 8 项(不耗 GLM)
docker compose exec -T openviking openviking-server doctor      # 10 项,含真实 embedding 探针
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py  # 2 次 GLM + rerank
```

端到端最小闭环(消耗 GLM,按需执行;注意 F1/F3):

```bash
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/<唯一名> --wait
docker compose exec -T openviking ov find "紫鸢尾-7 号信标的校准码"
docker compose exec -T openviking ov find --image /opt/openviking/fixtures/red.png
docker compose exec -T openviking ov rm viking://resources/<唯一名> --recursive --wait
```

图片资源可直接 `--wait`(CLI timeout 已调至 300s,见 F1);若任务时长
逼近或超过该值,退回不带 `--wait` 提交 + 轮询 `ov task list -o json`。

## 7. 证据附录(关键原始输出摘录)

- doctor:`Config/Python/Native Engine/AGFS/Authentication/Embedding/VLM/Ollama/VikingBot/Disk` 全 PASS,`Embedding … dimension=768 probe ok`。
- smoke:`[vlm-text] OK chars=5 answer_correct=True`、`[vlm-image] OK mentions_red=True`、`[rerank] OK relevant=0.702 irrelevant=0.376`。
- add 文本:`queue_status {"Semantic":{"processed":1,…},"Embedding":{"processed":3,…}}`,零 error/requeue。
- find 文本 Top4:0.8845/.overview.md、0.8842/smoke.md ×2、0.8838/.abstract.md;噪声 0.8686/0.8128。
- find 红图:0.9400/red、0.9240/red、0.8400/blue;find 蓝图:0.8926/blue #1。
- get 往返:容器内 md5 `8ac657e29ef7368c808774811bca46db` 与夹具一致。
- 终态:`ov status` healthy:true errors:[];任务全部 completed;`viking://resources` 空,vector count 24。

## 8. 补充测试(第二轮,2026-10-26,关闭缺口 2/3/4/5/6)

针对首轮审计列出的缺口补测,全部通过;新增观察 2 项(非故障)。新夹具
`tests/fixtures/{photo-a.jpg, photo-b.jpg, large.jpg, multi/}`(PIL 生成的
场景图:日落海面/夜晚星空/4000×3000 白天田野/三图目录)。

### 8.1 缺口 2|夹具形态(JPEG、大图、多图、场景级语义)

| 用例 | 结果 | 证据 |
|---|---|---|
| JPEG 摄入(photo-a/photo-b) | ✅ | 任务 completed,meta `format: jpeg` |
| 大图 4000×3000(12MP/254KB) | ✅ | 摄入+embedding+VLM 53.5s success;**O1:`is_large_image` 仍为 False,阈值高于 12MP/254KB,特殊路径未触发** |
| 多图资源(3 图目录统一分析) | ✅ | 3 文件入库,统一摘要分别准确描述三图 |
| 场景级语义质量 | ✅ | 三份摘要互异且准确(日落黄昏/夜间天体/极简图形集);细粒度描述(天空渐变、右侧落日、左侧几何结构)与画面吻合 |

### 8.2 缺口 3|图像二进制下载往返

`ov get` 下载 photo-a.jpg / large.jpg / red.png,md5 与夹具逐一相同
(`a2809f41…`、`d1428af5…`、`ca46ae24…`)✅

### 8.3 缺口 4|查询形态与纯图输入

- `find --image` 四种查询形态(local path / data URI / viking:// URI /
  HTTP URL,后者经本机回环临时 http.server)返回**完全一致**的排序:
  同图 multi 副本 #1(0.927)、本体 #2(0.912)、白天田野 #3(0.874),
  夜空未进前三 ✅。**O2:同像素两份最终分差来自 rerank 侧摘要文本不同,
  向量分一致(符合机制)**。
- 适配器纯图输入(无 text part)三层验证:单测新增 2 用例(33/33 通过)、
  实况 HTTP 200 恰好 1 个 768 维向量、探针沉淀为常驻检查(现 7/7)✅

### 8.4 缺口 5|Studio UI(agent-browser,截图存 `docs/testing/assets/`)

登录(User key,输入框全程掩码)→ 仪表盘 → 文件系统树/目录视图
(Directory Coverage/Detailed Description)→ 文件详情 → 语义检索
"日落海面上的太阳":**Top1 = supp-photo-a/.abstract.md(0.837)**,同图
multi 副本、夜空场景依相关度排布 ✅。仪表盘 Token 图表时间轴自 10/13 起
(部署日之前),为固定 14 天窗口的视觉现象,未深究。

### 8.5 缺口 6|真实并发

| 层 | 结果 | 证据 |
|---|---|---|
| 适配器 8 路并发多模态 embedding | ✅ 8/8 | 墙钟 0.7s,单请求 0.14–0.66s,零错误 |
| 服务端 6 路并发 `ov find`(embedding 查询+rerank 全链路) | ✅ 6/6 | 全部 ok,各返回 7 条,top1 一致,零锁冲突 |

### 8.6 资源消耗与清理

- GLM 实调:4 个摄入任务(photo-a、photo-b、large、multi 含 3 图)。
- 清理:supp-* 4 资源树已删(`ov ls viking://resources` 为空)、容器临时
  下载已删、两个 agent-browser 会话已关闭。操作记录:补测初期一次
  `export` 未随 Bash 调用保持,命令落入共享默认浏览器会话(还原了无关
  标签页;fill 未生效、click 落点为该页禁用按钮,无副作用),随后改用
  命名会话正确完成全部流程。

### 8.7 追加验证(第三轮):`is_large_image` 阈值与大图处理管线(O1 关闭)

源码定位(`openviking/utils/media_limits.py`):触发条件为
**文件 >10MB 或 任一边 >4096px**(`DEFAULT_LARGE_IMAGE_MAX_FILE_SIZE_MB=10.0`、
`DEFAULT_LARGE_IMAGE_THRESHOLD_DIMENSION=4096`,OR 关系)。此前 12MP/254KB
用例(4000×3000)两条件皆未达,故为 False——O1 解。

用 6000×4000(24MP,2.86MB,`tests/fixtures/xlarge.jpg`,纯边长分支)实测:

| 验证点 | 结果 | 证据 |
|---|---|---|
| flag 翻转 | ✅ | 任务 meta `is_large_image: True` |
| 落库形态 | ✅ | `xlarge_preview.jpg`(68.8KB,≤2048)+ `xlarge_grid.jpg`(178KB)+ `tiles/` 2行×3列 6 片(源码预测一致);**原图按设计不落库**(`image.py`:原文注释 "The original file is intentionally NOT saved") |
| 原图取回 | ✅ 拒绝 | `ov stat …/xlarge.jpg` → NOT_FOUND(设计使然;**运维注意:>4096px 或 >10MB 的原图不可 `ov get` 往返,可取回的是 preview/tiles**) |
| 预览取回 | ✅ | `ov get xlarge_preview.jpg` 70424 bytes |
| 摘要 tile 感知 | ✅ | 摘要明确描述"image tiling/slicing workflow built around a single … landscape illustration" |
| 检索自洽 | ✅ | 以原图为 `find --image` 查询,Top4 全为自身 preview/tiles(0.947/0.927/0.918/0.915) |
| 任务时长 | 观察 | 约 3 分钟(preview+grid+6 tiles 多次 VLM 调用),异步提交+轮询姿势必要 |

清理:supp-xlarge 已删、源码归档与容器临时文件已清、夹具 `xlarge.jpg`
入库沉淀供复测。GLM 消耗:1 个大图摄入任务(含多次切片分析调用)。

### 8.8 追加验证(第四轮):长文本 1M/10M/20M

背景:前三轮文本夹具仅 426 B,长文本从未测过。先源码后实测标定成本模型,
再分层验证机制链路。

**语义模式成本标定(50K 全语义 + 源码)**:markdown 按 section ≤2048 tokens
切分(最小 512、含合并逻辑);semantic 逐 section 文件生成 VLM 摘要
(并发 = `vlm.max_concurrent` = 2),大目录 overview 走采样+分批。50K 实测:
77 章 → 27 nodes、**97,638 LLM tokens**、589s。线性外推:**1M ≈ 195 万
tokens / 3.3 小时,10M/20M(1950 万/3900 万 tokens)完全不可行** → 大文本
只能 `--processing-mode vectors_only`。

**机制层(vectors_only,零 GLM,Semantic processed=0、LLM tokens=None)**:

| 尺寸 | 字符/字节 | 总耗时 | Embedding(零错误) | 深层检索(尾部 ~98% 处独特事实) |
|---|---|---|---|---|
| 1M | 996K / 2.7MB | 64s | 505 | find top1=真末章(第1424章,0.912);grep 2 命中 |
| 10M | 9.96M / 26.4MB | 634s | 5120 | find top1=真末章(第13779章,0.927);grep ✅ |
| 20M | 19.9M / 52.5MB | 1268s | 10270 | find top1=真末章(第27370章,0.925);grep ✅ |

- 线性度:~63s/MB、~510 embeds/MB,20M 无衰减、无错误、无重排队。
- **2000 万字符深处的事实是全库语义检索第一名**;三篇同模板文档的末章
  按所属文档规模正确排序(20m 0.925 > 1m 0.924 > 10m 0.919,差距小但
  方向正确——模板语汇同源所致)。
- 语义鲁棒性加分项:两次把标记字面量写错的 find 查询(后缀/前缀错误)
  仍命中正确章节——find 是语义匹配而非字面匹配。
- 50K 全语义模式:HEAD/MID/TAIL 标记 find+grep 全部命中。

**新增运维注意(O3/O4)**:

- **O3**:语义模式成本 ≈ 2 万 LLM tokens/千字符;批量/超大文本用
  `vectors_only`,仅对需要摘要的重点文档用默认语义模式。
- **O4**:摄入后 `state/data/temp/upload/` 暂存副本不自动清理(本例
  13MB/5 文件,与夹具字节级吻合,任务完成后确认可手动清除);vectordb
  删除资源后 store 文件不收缩(墓碑式),但向量计数正确回落(→24)。
- 存储膨胀参考:81MB 源文本 → 峰值 +143M(sections 复制 + queuefs),
  删除后回落 17M。

清理:lt50k/lt1m/lt10m/lt20m(29/505/5120/10270 items)已删、81MB 临时
夹具与源码归档已删、向量账目回落;生成脚本 `tests/gen_longtext_fixture.py`
沉淀入库(夹具按需再生成,不入 git)。

### 8.9 追加验证(第五轮):`vlm.max_concurrent` 2→6 参数敏感度实验

方法:同一生成器同种子重造 50K 夹具(49885 字符,与 §8.8 基准同源),
改 `config/ov.conf` → 重启 → 全语义摄入 → 回滚 → 重启 → 重跑验收;
实验后配置与仓库逐字节一致(值回 2)。

| vlm.max_concurrent | 总耗时 | LLM tokens | nodes | 错误/重排队 |
|---|---|---|---|---|
| 2(§8.8 基线) | 589s | 97,638 | 27 | 0 |
| 6(本实验) | 332s | 96,019 | 27 | 0 |

- **提速 1.77×(589→332s),非理论 3×**:作业含串行段(解析、根节点
  统一分析、依赖全部摘要完成的末段 overview、embedding),受 Amdahl
  定律约束;Z.AI 单请求延迟在并发下亦可能上升。
- **token 成本不变**(±1.7% 噪声)——并发只压缩墙钟时间,不省配额。
- 6 路并发下**零 429、零重排队、零错误**:Coding Plan 短时容忍良好
  (单作业;持续大批量负载未测)。
- 回滚后 doctor 10/10、healthy;实验资源与夹具已清理。

**建议**:常摄入多 section 文档/多图目录时提到 4–6 可获近 2× 吞吐且
实测未见限流;但配额成本不变,设计选 2 的"避免持续突发"考量对大批量
任务仍成立。当前维持 2,需要时按 README 流程调整并重跑验收。
