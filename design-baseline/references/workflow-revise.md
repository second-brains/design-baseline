# 修订与基线化流程

1. **定位根因**：确定本次变更的事实 ID（一个根因一个 CP，跨文件批量）。
2. `db.py cp new --id CP-0042 --root-cause "ROLE-003 增加数据范围约束"`
3. **先改事实，再改叙述**：patches 顺序为 facts → 下游 docs。用 `edit` 增量，
   find 取唯一可定位的原文片段；新文件用 `create`，内容放 `design/proposals/CP-0042/`。
4. `db.py cp validate --id CP-0042` —— 必须 0 error（find 命中数、路径、动作合法性）。
5. `db.py cp hash --id CP-0042` —— 把输出的 JSON 骨架 + 变更摘要 + 受影响文件清单呈报用户。
   **停在此处等待用户批准，不得自行填写 approved_by。**
6. 用户保存 `CP-0042.approval.json` 后：
   `db.py cp apply --id CP-0042 --approval design/proposals/CP-0042.approval.json`
   （校验批准哈希 → 应用 → 写不可变对象 → 原子切指针）
7. `db.py propagate` —— 元素级 diff 打疑点，生成 `trace/pending.jsonl`。
8. 按 `l2-pair-check.md` 逐对核对 → `db.py verdict`；判定 drift 的另开 CP。
9. `db.py gate` —— 必须 0，方可提交。

## 新建文档
草稿期自由编辑（未基线路径不受约束）；基线化同样走 CP（`action: create`）+ 批准。

## 失败恢复
apply 中途崩溃会留下工作视图与指针不一致 → `db.py lint` 报"原地修订" → 用
`design/store/` 中对应哈希的对象恢复工作视图，或重新 apply（内容寻址，幂等）。