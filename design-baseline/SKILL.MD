---
name: design-baseline
description: 文件驱动设计的基线只读与跨文档一致性管理。用于初始化项目（PRD/用户故事/低保真的目录、事实基线、追溯与校验）、修订已基线文档、检测文档间漂移、生成与应用变更提案。当用户提到基线只读、禁止原文修订、版本同步、设计文档初始化、需求追溯、一致性检查时使用。
---


# design-baseline


## 硬规则（任何模式下不可违反）


1. **禁止修改已基线内容**。工作视图必须与基线版本逐字节一致；修订一律走变更提案 CP。
2. **你无权批准 CP**。检测到漂移只生成提案并呈报用户；`cp apply` 必须携带用户批准凭证。
3. **事实单源**。跨文档共享的事实只写 `design/facts/`，叙述文档用 ID 引用，禁止复制事实内容。生成中新发现的事实进 `design/inbox/`，不得悄悄写进散文。
4. **状态外置**。一致性结论只写 `design/trace/ledger.jsonl`；禁止改动 `design/store/`（不可变版本对象）。
5. **不做全量语义审查**。只核对 `trace/pending.jsonl` 中的成对元素，每对一次封闭式提问。
6. **验证证据必须新鲜**。执行型检查用 `verify run` 实际运行；基线、检查定义/模式、checker、环境、输入或日志变化都会使证据失效。未评估不是通过。




## 命令路由

工具优先用仓库内副本 `design/tools/db.py`（未初始化时用本 Skill 的 `scripts/db.py`）。

| 意图 | 动作 |
|---|---|
| 初始化项目 | 读 `assets/PRINCIPLES.md` → `db.py init --dry-run` → 呈报 → `db.py init` |
| 新建文档（草稿） | 复制 `design/templates/doc.md`，填 front-matter；草稿区自由编辑 |
| 基线化 / 修订 | 读 `references/workflow-revise.md` → `cp new` → 填 patches → `cp validate` → `cp hash` 呈报用户 → 用户批准后 `cp apply` |
| 一致性检查 | `db.py lint` → `graph` → `propagate`；对 `pending.jsonl` 每对按 `references/l2-pair-check.md` 核对 → `db.py verdict` |
| 登记跨对象约束 | `db.py check add --id ... --description ... --command ... --input ...`（先 observe）→ `db.py verify run` → `check enforce` → 再 run |
| 人工语义复核 | 显式 `check add ... --manual` → `verify manual` 记录结果与证据；不能替代可执行检查 |
| 提交前门禁 | `db.py gate`（L0+L1+疑点清零） |
| 查看现状 | `db.py status` |

字段定义见 `references/schemas.md`（按需读取，勿预加载）。


## 完成标准


- 基线化门禁：`gate` 退出码 0 —— L0/L1 全绿，覆盖清单有匹配对象，新旧关系已传播，enforce 检查有当前版本的成功证据，且每个开放疑点都有 `verdict=consistent` 或有效 `waiver`。
- 任一跨文档差异必须处于四态之一：`omits` / `suspect` / `CP` / `waiver`。四者之外发现差异即为流程缺陷，须报告而非静默修补。




## Token 纪律


- L0/L1 由脚本完成（确定性、零 token）；LLM 只用于 L2 成对核对与撰写叙述。
- 修订用 `action: edit` 的 find/replace 增量，输出量 O(改动量)；仅新建文件用 `create`。
- 生成下游文档时按 ID 检索上游元素，不整篇灌入散文。
