## 设计基线（强制，适用于所有 agent 与人类协作者）

本仓库 `design/` 遵循 `design/PRINCIPLES.md`：**基线只读，修订必须经变更提案 CP + 人工批准**。


- 事实单源：跨文档事实只写 `design/facts/`，叙述用 ID 引用；新事实进 `design/inbox/`。
- 检查：`python3 design/tools/db.py gate`（提交前必须为 0）
- 修订：`python3 design/tools/db.py cp new --id CP-XXXX --root-cause "..."`
- 一致性状态只写 `design/trace/ledger.jsonl`；禁止改动已基线内容与 `design/store/`。
- agent 无权批准 CP；检测到漂移只生成提案并呈报用户。
