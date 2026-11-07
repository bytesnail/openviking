# 检索与 VLM 链路运行事实(活文档)

> 定位:本栈检索/压缩/VLM 链路的运维必需结论与论据(2026-11-07 核查、2026-11-08 embedding 预算落地)。操作值以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

逐项证据与实测数据见
[归档仓结论核查与 embedding 截断标定](../research/2026-11-07-archive-conclusion-verification.md);
此处只留运维必需结论。

- **query expansion 成功时日志静默**:状态只在检索响应体 `query_expansion`
  字段;失败才落 `Query expansion failed` WARNING。验收判据用"带
  `session_id` 的 context 检索无该 WARNING"——**不存在"日志变 used"这个
  信号**。expansion 仅在请求带 `session_id` 且会话有内容时尝试,不带
  session 的检索静默跳过。
- **query_planner 段整段替换 vlm**:该段一旦有配置,未配字段不继承 vlm
  而走自身默认(timeout 600s、max_concurrent 32);`max_tokens` 未配则
  不发该参数(由 z.ai 端默认决定)。当前 ov.conf 显式只配
  provider/model/api_base/api_key/extra_request_body。
- **`${VAR}` 展开静默残留**:`expandvars` 对未设置变量保留字面 `${VAR}`
  且容器照常 healthy;变量名拼错表现为对应后端静默 401,只能靠
  doctor/真实调用发现,启动日志无信号。
- **ov.conf 无热加载,`docker compose restart openviking` 即生效**:单文件
  bind 在容器重启时重新解析 inode(2026-11-07 实测:重启后容器内挂载
  md5 与宿主一致、新配置已加载)。旧归档仓"restart 无效须 down/up"
  说法不实,勿再引用;真实坑仅是"编辑后不重启"。
- **z.ai/glm-5.3-flash**:thinking 与 content 共享输出预算(日志实证
  `finish_reason=length` 且 content 空);coding 端点实测接受
  `thinking.type=disabled`(与 z.ai 文档页矛盾,以实测为准,thinking 是否
  真被关闭未能从外侧证实);vlm 段无法真正关思考,靠 `vlm.max_tokens`
  容纳。`vlm.max_tokens=65536`(2026-11-07 22:25 起)的验收判据:会话
  结束的 Phase 2 记忆提取不再出现 `output cap=` 截断 ERROR。
- **embedding 截断标定**(2026-11-07,生产路径实测 `usage.prompt_tokens`):
  估算器按 CJK 1 字=1 token、ASCII 4 字符=1 token;本栈真实卡实际/估算
  1.02–1.17(5 张大卡全实测),纯中文 0.77、英文散文 0.59、真实代码/脚本
  (Python/bash)1.03–1.22、紧凑配置(YAML)~1.39、符号密集合成样式 1.83、
  minified JSON ~2.08、base64 ≳2.75(后两类在现行 4096 下即失败,非提
  预算所引入;上游 #5895 的低估因子来自其他 tokenizer,不适用本栈)。
  记忆卡语料核查:126 张中仅 15 张含 fenced 块,最大单块 est 2234——
  "记忆卡整段引用代码"在真实语料中不成立。**已落地 6000(2026-11-08,
  取"全库最大卡 est 5790 全覆盖"与"YAML 因子 1.39 安全线 6078"的交集;
  restart 后加载值核实,随对 `viking://user/meteor` 执行 `ov reindex
  --mode vectors_only --force` 重建 154 条、0 失败)**;仅紧凑配置类
  est>~6078 顶部刀锋越界,失败为适配器可见 502。验收时注意**文档级向量
  语义稀释**:尾部"段落级主题"召回改善,标识符级细节查询(write_done 类
  探针)在 4096/6000 下均难命中——该类需求属 sparse 职责,本地仍不可用;
  卡的 abstract 标量(VLM 全卡压缩、不截断)是既有 L1 补偿通道。
- **reindex 须以目标空间属主身份执行**:0.5.x 起 root key 被拒于租户
  数据面("ROOT API keys cannot access tenant-scoped data APIs"),
  default(admin) 亦无他人空间读权;`ov` CLI 配置文件会压住
  `OPENVIKING_API_KEY` 等 env 传入。做法:容器 /tmp 建 600 临时 CLI 配置
  (身份=目标 user,key 经 env 中转、不经 argv)→ `OPENVIKING_CLI_CONFIG_FILE`
  指向它执行 → 用后即删(与 check_user 同款模式,2026-11-08 实测)。
- 上游未修锚点(2026-11-07):#3101(关机 flush 失败,索引中断源)、
  #5895/#5760/#6418(截断估算类)、sparse 本地 dense-only(#6109 收敛
  方向未合并、#7162 hybrid 损坏未修)。
