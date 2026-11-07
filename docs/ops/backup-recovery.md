# 备份、恢复演练与 vectordb 恢复(活文档)

> 定位:备份/恢复的完整流程与判据。操作值以 `README.md` 注册表、`compose.yaml`、`config/ov.conf` 三处为权威;本文随实施同步维护,不按日期归档。最后核对:2026-11-08。

## 备份

```bash
docker compose stop
TS=$(date +%Y%m%d-%H%M%S)
tar -czf "backups/openviking-${TS}.tar.gz" state config scripts tests compose.yaml secrets.env
chmod 600 "backups/openviking-${TS}.tar.gz"
docker compose up -d --wait --wait-timeout 180
```

备份含密钥:不打印内容、不上传、不入库;包含 `tests/` 是为了恢复后能使用
同一套 fixtures、smoke 与 E2E 验收。

## 恢复演练

恢复演练**不得与生产栈并行**:先执行 `docker compose stop` 并保持生产停止,
确认 1933/18353 未被其他进程占用;再在新空临时目录解包,使用独立 Compose
project name,沿用备份中的 loopback 端口与 CLI URL 完成验收。这样 restored
`ovcli.conf`、health/curl 与固定 URI 写入都不可能打到在用生产。验收后先
`docker compose down` 停止恢复栈,确认临时端口已释放,再回原目录启动生产;
全过程不得覆盖原 `state/`。若确需并行恢复,必须另行显式改写 Compose、
ov.conf、active/saved CLI URL 及全部探针端点,不能只改两个监听端口。

## vectordb 索引损坏恢复(v0.5.2.1 源码核实)

症状:容器 crash loop(引擎层 meta 加载失败异常直接传播),常见诱因是
异常中断(断电/OOM/`docker kill`)留下 0 字节
`state/data/vectordb/context/index/default/versions/<纳秒ts>/manager_meta.json`;
上游 #3101(SIGTERM 时 flush/close 失败)仍是现存中断源。恢复要点
(与 0.4.x 时代的关键差异:**不依赖未损坏旧快照**):

1. 停栈并先备份:`cp -a state/data/vectordb/context <临时备份目录>`(或走
   上节全量备份);任何 vectordb 状态改动前必做。
2. 定位损坏版本:
   `find state/data/vectordb/context/index/default/versions -name manager_meta.json -size 0`。
3. 删除损坏版本目录及对应 `<ts>.write_done` 标记;**必须保留
   `index/default/index_meta.json`**——它缺失时启动会跳过 recovery。
4. 重启:启动路径 `_recover()` 从 LevelDB store(`context/store/`)全量
   重建并重放 delta(日志特征 `Index 'default': replaying delta records
   lazily` → `replay complete`),数据本体(store/)不受索引损坏影响。
5. 验收:doctor + 冒烟检索命中;必要时 `content reindex`(`mode=
   vectors_only`,0.5.2.1 下真重建向量)兜底重算。

注意:正常 `close()` 只保留最新一个版本快照,**多版本快照不是安全网,
`backups/` 才是**;"删整个 index/ 指望重建"与"reindex 不生成向量"均为
0.4.x 旧说,0.5.2.1 已不成立(详证见
[归档仓结论核查](../research/2026-11-07-archive-conclusion-verification.md) §4)。
