# 修订与基线化流程

1. **定位根因**：确定本次变更的事实 ID（一个根因一个 CP，跨文件批量）。
2. `db.py cp new --id CP-0042 --root-cause "ROLE-003 增加数据范围约束"`
3. **先改事实，再改叙述**：patches 顺序为 facts → 下游 docs。用 `edit` 增量，
   find 取唯一可定位的原文片段；新文件用 `create`，内容放 `design/proposals/CP-0042/`。
4. `db.py cp validate --id CP-0042` —— 必须 0 error（find 命中数、路径、动作合法性）。
5. `db.py cp hash --id CP-0042` —— 把输出的 JSON 骨架、批准范围和变更摘要呈报用户。批准范围绑定 CP YAML、edit 源、create 输入和实际 payload 的哈希。
   **停在此处等待用户批准，不得自行填写 approved_by。**
6. 用户保存 `CP-0042.approval.json` 后：
   `db.py cp apply --id CP-0042 --approval design/proposals/CP-0042.approval.json`
   （重算批准范围并校验 → 应用全部文件 → 写不可变对象 → 原子切指针）
7. `db.py propagate` —— 元素级 diff 打疑点，生成 `trace/pending.jsonl`。
8. 按 `l2-pair-check.md` 逐对核对 → `db.py verdict`；判定 drift 的另开 CP。
9. `db.py gate` —— 必须 0，方可提交。

## 全局约束与验证门禁

覆盖清单声明在 `manifest.json coverage[]`，每项选择上游范围、下游类型和正整数最小数量；匹配对象必须实际存在，空清单/空选择范围不代表通过。传播保存新旧图，删边也会进入疑点，仍按 pending 中的配对做 L2。

跨对象约束登记为检查，新增时默认为 observe。样例检查器会确认每项 REQ 至少关联一份 story；它读取的测试代码及目录都显式加入输入哈希：

```bash
python3 design/tools/db.py check add --id GLOBAL-REQ-STORY --description "每项需求至少关联一份用户故事" --command python3 design/tools/global_coverage.py --input design/tools/global_coverage.py --input design/facts --input design/docs --method e2e
python3 design/tools/db.py verify run --check GLOBAL-REQ-STORY
# observe 结果确认后升级，升级会使旧证据失效，因此再执行一次
python3 design/tools/db.py check enforce --id GLOBAL-REQ-STORY
python3 design/tools/db.py verify run --check GLOBAL-REQ-STORY
python3 design/tools/db.py gate
```

runner 使用 argv 和 `shell=False`，保存退出码及日志哈希。命令、检查定义/模式、当前所有 artifact 版本、checker、运行环境、输入或输出变化都会使证据失效；任一 artifact 变化都会保守地使全部检查失效。人工语义复核需显式注册 `--manual` 并用 `verify manual`，不能为可执行检查手填 pass。observe 的 fail/unknown 仅 WARN，不能当成检查通过。

若执行中的检查试图修改 manifest，runner 会恢复原内容并让 gate 阻断。随后可对相同检查定义成功重跑，或由人工检查原因后执行 `check resolve --id GLOBAL-REQ-STORY --reason "..."`。这保护的是 runner 执行中的策略；不提供身份签名，仓库写权限用户仍可能直接修改 manifest 或 ledger，因此需要仓库审查/CI 分支保护。本轮只对已登记对象与显式检查范围负责，不宣称绝对覆盖。

## 新建文档
草稿期自由编辑（未基线路径不受约束）；基线化同样走 CP（`action: create`）+ 批准。

## 失败恢复
apply 中途崩溃会留下工作视图与指针不一致 → `db.py lint` 报"原地修订" → 用
`design/store/` 中对应哈希的对象恢复工作视图，或重新 apply（内容寻址，幂等）。
