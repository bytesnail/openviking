## 仓库性质

OpenViking v0.5.2.1 本机单用户 Docker Compose 部署的**运维仓**:配置、部署/验收脚本、测试与决策档案,不是 OpenViking 应用源码。当前操作值的权威来源只有三处:`README.md`(索引与操作值注册表:端口拓扑权威表、三身份与凭据位置、验收判据入口)、`compose.yaml`、`config/ov.conf`。流程与判据细则在 `docs/ops/` 活文档(部署、验收、备份恢复、升级、Agent 接入、运行事实),随实施同步维护;其余 `docs/`(testing、research、superpowers/specs、superpowers/plans,按日期命名)是历史决策与验收档案,其中示例值可能已被后续实施取代。改任何操作值前先读 README 对应章节,并在同一变更中同步更新。

## 常用命令

```bash
# 单元测试:离线自足(本地假上游/stub/tmp 沙盒),无需栈在位
python3 -m pytest tests/
python3 -m pytest tests/test_env_file.py -k parse   # 单个测试

# e2e:需 compose 栈与 model-serving(18351/18352)在位,发真实请求
python3 tests/e2e_adapter_probe.py
REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh
USER_A=default USER_B=test \
  USER_A_KEY_FILE=state/secrets/user_api_key \
  USER_B_KEY_FILE=state/secrets/test_user_api_key \
  bash tests/e2e_user_isolation.sh

# 栈验收(doctor 的 embedding 探针与 smoke_models 的 2×GLM+1×rerank
# 均为真实调用,消耗 GLM 用量;完整清单与判据见 docs/ops/acceptance.md)
docker compose exec -T openviking openviking-server doctor
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py

# 部署前置检查:幂等,但必须在栈停止时运行(要求 1933/18353 空闲)
./scripts/prepare.sh
```

仓库无构建系统与 lint 配置;Python 脚本直接运行。`tests/conftest.py` 把 `scripts/` 加入 sys.path,故测试直接 `import embedding_adapter` 等部署脚本。

## 架构

同一镜像(tag+digest 固定,不拉 latest)跑两个 service,全部只监听 127.0.0.1,host 网络:

- `openviking`(API :1933,uid 1000):服务器本体;`state/` 为其工作区(挂载为容器 `/app/.openviking`,敏感 700)。
- `embedding-adapter`(:18353,uid 65532,降权 + cap_drop):运行 `scripts/embedding_adapter.py`,把 OpenViking 的多模态 embedding 请求纯文本化后转发给 model-serving 仓(另一仓库)的 embeddinggemma-2(:18351)。reranker(:18352)由 OpenViking 直连,不经适配器;VLM 走 z.ai GLM,key 经 `config/ov.conf` 的 `${VAR}` 引用 `secrets.env`。

**端口 1933/18351/18352/18353 任一变更,须同步 README「端口拓扑权威表」列出的全部落点**(compose、prepare.sh、bootstrap_auth.py、env_file.py、ov.conf 及 e2e 兜底默认);漏改后果从硬失败到静默降级不一。

服务端三个身份决定用哪把 key(完整凭据位置见 README「三身份与凭据位置」):

| 身份 | user_id/role | 用途 |
|---|---|---|
| 部署运维 | `default`/admin | bootstrap、doctor/smoke、部署验收 |
| 正式个人 | `meteor`/user | Claude Code/OpenCode 真实记忆(key 只在宿主 `~/.openviking/ovcli.conf`) |
| 测试 | `test`/user | 一切写数据的功能测试、隔离与插件实验 |

`scripts/bootstrap_auth.py`(容器内认证初始化,退出码阶梯见 docs/ops/deploy.md)、`scripts/env_file.py`(secrets/配置只读解析校验,绝不回显值)、`scripts/smoke_models.py` 均以容器内路径 `/opt/openviking/scripts/` 调用——宿主 `scripts/` 与 `tests/fixtures/` 只读挂载进容器。Agent(Claude Code/OpenCode)接入基线与插件升级验收见 docs/ops/agent-integration.md。

## 硬性纪律

- **凭据文件一律不读、不回显**:`secrets.env`、`state/secrets/*`、`state/ovcli.conf*`、`state/data/viking/default/_system/users.json`、`backups/*`。需要值时走变量中转(`K=$(< file)` 后引用 `$K`),命令文本与输出均不得出现明文;容器内不跑 `env`/`printenv`,不输出完整 `docker inspect`。
- **写数据或验证用户数据面的测试一律 test 身份**,目标 `viking://user/test/...`;不写 `viking://agent/*`,不写 meteor 真实记忆。固定冒烟 URI `viking://resources/deployment-smoke*` 是替换语义,勿指向真实数据。
- root/default key 可经 Admin API 读取全部 user key(crown-jewel),不进 Agent 与测试夹具。
- `config/ov.conf` 无热加载:编辑后 `docker compose restart openviking` 生效。
- 提交直接进 main(用户既定流程,不另开分支);message 用 feat/fix/docs 等前缀,操作值变更随提交同步 README。
