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
| User key(X-API-Key 与 Bearer)→ tenant API | ✅ 200 |
| Root key → tenant API | ✅ 403(与源码论断 24 一致:ROOT 不绑租户) |
| Root key → `/api/v1/admin/accounts` | ✅ 200 |
| User key → admin API | ✅ 403 |
| 文件权限 | ✅ `state`/`state/secrets` 700,`user_api_key`/`ovcli.conf`(.local)/`secrets.env` 600 |
| 容器加固 | ✅ 适配器 readonly rootfs + cap_drop ALL + no-new-privileges + uid 65532;主服务 uid 1000 |

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
python3 tests/e2e_adapter_probe.py                 # 适配器实况 6 项(需服务在位,不耗 GLM)
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
