# 升级与回滚(活文档)

> 定位:升级/回滚流程与 embedding 模型变更约束。操作值以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

## 流程

1. 先备份(见 [backup-recovery.md](backup-recovery.md))。
2. 人工选定新 release,核对官方 tag、源码与镜像 digest;不拉 `latest`。
3. 更新 `compose.yaml` 的 tag+digest,复核 `ov.conf` schema 与适配器协议。
4. 启动后重跑全部验收(见 [acceptance.md](acceptance.md))。

## 回滚

停止并保留异常状态 → 恢复与旧镜像匹配的配置与状态备份 → 旧 tag+digest
启动 → 重跑验收;不只回滚镜像而保留可能不兼容的新状态。

## Embedding 模型/维度变更约束

Embedding 模型或维度变化必须走官方迁移/reindex 或新建空工作区;同维度
换模型/端点可配 `embedding.allow_metadata_override` 保留已索引数据
(改写 collection 元数据并告警,v0.5.2.1 schema),仅维度变化必须重建
(`collection_meta.json` 存 Dim 并严格校验)。reindex 须以目标空间属主
身份执行,细则见 [runtime-behavior.md](runtime-behavior.md)。
