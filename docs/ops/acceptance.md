# 验收与测试身份纪律(活文档)

> 定位:验收命令、完整判据与测试身份纪律。操作值以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

## 栈验收

```bash
docker compose exec -T openviking openviking-server doctor
docker compose exec -T openviking python -I -B /opt/openviking/scripts/smoke_models.py
docker compose exec -T openviking ov health -o json
curl -fsS http://127.0.0.1:1933/health
curl -fsS http://127.0.0.1:1933/ready
```

- `doctor` 须全项通过(embedding 探针为真实请求,返回 768 维)。
- `smoke_models.py` 发起 2 次 GLM 真实调用与 1 次 rerank 真实请求,消耗少量
  Coding Plan 用量;`ov health -o json` 须含 `"healthy": true`,且
  `result.account_id/user_id` 均为 `default`、`result.role` 必须为 `admin`
  (**`role=root` 或降级后的 `role=user` 均判失败**:root-only 配置也会返回
  default/default/healthy,而 USER default 无法执行所需运维管理)。
- 扩展功能面(2026-10-26 两轮全量通过):`python3 tests/e2e_adapter_probe.py`
  (适配器实况 8 项,含配置模型名与 /v1/models 加载列表比对)与
  `bash tests/e2e_auth_matrix.sh`(基础认证边界 9 项 + 4 项 USER 边界);
  本部署已创建 test,完整验收统一用 `REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh`,
  使缺失 USER 行直接失败。矩阵只覆盖状态边界,不替代上述 default/test
  私有读写隔离检查;USER 不可访问账户管理接口的断言对应 v0.5.2.1。
  矩阵的 `BASE` 仅允许 loopback HTTP URL,防止把 ROOT/ADMIN key 发往远端;
  curl 以 `-q` 禁用用户 curlrc。完整矩阵、发现(F1–F4)与复测方法见
  `../testing/2026-10-26-openviking-functional-test.md`。

## 端到端(§9.4)

```bash
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/smoke.md --to viking://resources/deployment-smoke --wait
docker compose exec -T openviking ov add-resource /opt/openviking/fixtures/red.png --to viking://resources/deployment-smoke-image --wait
docker compose exec -T openviking ov find "紫鸢尾-7 号信标的校准码"
docker compose exec -T openviking ov ls viking://resources
```

- `--to` 指向已存在 URI 时是"替换"语义,旧内容会被更新掉:上述固定冒烟
  URI 在当前基线中已存在(见方案 §6.3),仅用于冒烟资源,勿指向真实数据;
  真实资源必须使用不存在的新 URI(多资源导入每资源独立 `--to`,或先
  `mkdir` 再用 `--parent`);若需保留旧冒烟内容,改用唯一 URI 或先删除。
- 大图(>4096px 任一边或 >10MB)摄入走切片管线:落库的是低清预览 +
  网格图 + tiles,**原图按设计不保存、不可 `ov get` 取回**;此类任务
  约 3 分钟,建议异步提交后轮询 `ov task list`(详见测试报告 §8.7)。
- 长文本语义成本 ≈ 0.195 万(约 1950)LLM tokens/千字符(50K 实测 9.7 万 tokens/10
  分钟;线性),1M 即约 195 万 tokens/3.3 小时;批量或超大文本用
  `--processing-mode vectors_only`(实测 20M 字符 21 分钟、零 GLM、深层
  检索正常),仅重点文档用默认语义模式(详见测试报告 §8.8)。
- 图片资源语义任务实测 64–110s,曾超过 CLI 60s 默认超时导致 `--wait` 报
  DEADLINE_EXCEEDED(服务端任务仍会完成)。CLI 顶层 `"timeout": 300`
  (无旗标,`ov config edit` 不含该项;`ov config show` 确认实际生效)
  现由 `bootstrap_auth.py` 生成与修复 `ovcli.conf` 时自动写入(已设置有限且
  `>=300` 的值不覆盖);active 与 saved profile 均由 bootstrap 同步处理,无需手工
  补写副本。若未来任务更慢仍超时,可继续调高该值,或退回不带 `--wait`
  提交 + 轮询 `ov task list`。
  同目录变更任务在途时,紧随的变更可能报 `[CONFLICT] lock acquire timed out`,
  排空后重试即可。

冒烟资源保留供检查;确认 URI 后可单独删除,不做自动递归清理
(2026-10-26 功能测试后已按此删除并验证,可用上述命令重建)。

## 测试身份纪律

凡会写数据或验证用户数据面的功能测试一律使用 test 身份,目标优先
`viking://user/test/resources/...`;不写 `viking://agent/*`,不写真实记忆。
项目脚本 source 不含密钥加载逻辑的 `tests/curl_auth.sh`,以 direct curl、
stdin `-K -` 传 key,不得 source 整个认证矩阵。共享区写入仅限专门的共享语义
测试,并须使用专用 URI 和验收后清理。

```bash
# test 身份健康检查:临时配置位于容器 /tmp,命令完成后即删除
docker compose exec -T openviking python -I -B \
  /opt/openviking/scripts/bootstrap_auth.py check-user \
  --user-id test --role user \
  --key-file /app/.openviking/secrets/test_user_api_key \
  --config-file /tmp/ovcli.test.conf

# 强制 USER 权限边界 + default/test 双向私有隔离与夹具清理
REQUIRE_USER=1 bash tests/e2e_auth_matrix.sh
USER_A=default USER_B=test \
  USER_A_KEY_FILE=state/secrets/user_api_key \
  USER_B_KEY_FILE=state/secrets/test_user_api_key \
  bash tests/e2e_user_isolation.sh
```

容器 `/tmp` 按 `compose.yaml` 为 tmpfs(`rw,noexec,nosuid`:openviking 256m、
embedding-adapter 16m):tmpfs 数据随容器停止清空、计入容器内存且有容量上限,
check-user 的临时身份配置因此不跨重启残留,但也不得把 `/tmp` 当持久存储。
上述隔离验收只证明普通数据面私有读写,不构成对 ADMIN/ROOT 或同 UID
进程的安全边界。
