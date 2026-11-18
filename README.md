# design-baseline
Try to solve the dilemma of constantly struggling with inconsistent document versions, repeated communication, and repeated global reviews during the system design process
按"装在哪 → 谁干什么 → 完整走一遍 → 日常怎么说"四步讲。核心心法：**你只做两件事——做设计决策、批准 CP；其余全部交给 agent 和脚本。**

## 一、安装（两种形态，都可用）

**形态 A：agent 环境支持 Agent Skills**（Claude Code、Qoder 等兼容 SKILL.md 约定的运行时）

```bash
# 项目级（推荐，随仓库走）或用户级，按你的运行时文档放：
your-project/.claude/skills/design-baseline/   # 放入上一轮生成的整个目录
pip install pyyaml
```

之后 agent 会在你提到"初始化设计基线 / 修订 PRD / 一致性检查"等场景时自动加载；也可显式说"使用 design-baseline skill"。

**形态 B：环境不支持 Skill**——没关系，这正是当初把强制力放仓库的原因。手动跑一次 init 后，仓库自包含（PRINCIPLES.md + AGENTS.md + db.py + hooks + CI），任何 agent 甚至纯人工都能照章办事，Skill 目录只是说明书。

## 二、分工

| 角色 | 职责 | 禁止 |
|---|---|---|
| **你** | 设计决策；填写批准凭证；必要时给豁免 | — |
| **agent** | 写草稿、填 patches、跑命令、L2 成对核对、呈报提案 | 批准 CP、改已基线内容 |
| **db.py / hooks / CI** | L0/L1 确定性校验、疑点传播、合成版本、原子切指针、阻断违规 | — |

## 三、完整走一遍（以"订单导出"为例）

所有命令在**仓库根目录**执行。

**Step 0：初始化**

```bash
git init          # 先有 git，hook 才生效
python3 /path/to/design-baseline/scripts/db.py init --dry-run   # 看清单
python3 /path/to/design-baseline/scripts/db.py init
```

此后一律用仓库内副本 `design/tools/db.py`。

**Step 1：填事实基线（唯一权威）**

让 agent 或你自己填 `design/facts/`：

```yaml
# requirements.yaml
REQ-001:
  summary: 区域经理可导出本部门订单
  actor: ROLE-003
# roles.yaml
ROLE-003:
  summary: 区域经理
  data_scope: 仅本部门
```

**Step 2：写草稿（草稿区自由编辑）**

草稿写在 `design/inbox/`，带 front-matter，**引用 ID、不复制事实**：

```yaml
# design/inbox/US-001.md
---
id: US-001
type: story
based_on: [REQ-001@v1]
uses_facts: [ROLE-003]
omits: [{fact: RULE-008, reason: 低保真阶段不涉及审批流}]
---
作为 ROLE-003，我希望导出订单，范围遵循事实基线中的数据范围约束。
```

**Step 3：首次基线化（一个 CP 批量打包）**

对 agent 说："把事实基线和这三份草稿基线化，根因是'初始设计基线'"。agent 会：

```bash
python3 design/tools/db.py cp new --id CP-0001 --root-cause "初始设计基线"
# 填 patches：facts 用 create；草稿用 create，content 指向 inbox 路径，path 指向正式路径
#   - path: docs/stories/US-001.md
#     action: create
#     content: design/inbox/US-001.md
python3 design/tools/db.py cp validate --id CP-0001
python3 design/tools/db.py cp hash --id CP-0001      # 输出 cp_hash + 批准骨架
```

**Step 4：你批准（唯一不可委托的动作）**

agent 呈报变更摘要后，**由你**把骨架填完存为 `design/proposals/CP-0001.approval.json`：

```json
{"cp":"CP-0001","cp_hash":"<hash命令的输出>","approved_by":"你的名字",
 "approved_at":"2026-11-18T10:30:00","statement":"我批准本提案全部 patches"}
```

**Step 5：应用 + 传播 + 门禁**

```bash
python3 design/tools/db.py cp apply --id CP-0001 --approval design/proposals/CP-0001.approval.json
python3 design/tools/db.py propagate
python3 design/tools/db.py gate        # 必须为 0
git add -A && git commit -m "CP-0001 初始设计基线"   # pre-commit 自动再跑 gate
```

基线化后可删除 inbox 里的草稿副本（inbox 是豁免区）。

**Step 6：日常修订循环（这才是系统的常态）**

你说："ROLE-003 的数据范围要收紧为'仅本人经手'，同步所有设计文档。"agent 执行：

```text
cp new CP-0002（根因=ROLE-003 数据范围变更）
→ edit patches：先 facts/roles.yaml，再沿追溯图找到的 US-001、SCR-001…
→ validate → hash → 呈报给你
→ 你填 approval.json（一次批准，覆盖全部受影响文件）
→ apply → propagate（只有可达下游被标 suspect）
→ 对 pending.jsonl 逐对 L2 核对 → verdict 写回
→ gate=0 → commit
```

若某对核对判 drift：另开 CP；若暂不处理：`db.py waiver --up ROLE-003 --down US-001 --owner 你 --expires 2026-12-01 --reason "..."`（必须有期限）。

## 四、日常触发语速查

| 你说 | agent 做 |
|---|---|
| "初始化设计基线" | init（先 dry-run 呈报） |
| "写 US-002 草稿，基于 REQ-001" | 按 ID 检索上游 → inbox 草稿 |
| "把这个改动同步到所有文档" | cp new → patches → validate → hash → **停下等你批** |
| "我批了 CP-0002" | apply → propagate → L2 → gate |
| "跑一致性检查" | lint → graph → propagate → 只核对 pending 对 |
| "这条缺口先欠着，月底还" | waiver（落盘、有期限） |
| "现在什么状态" | status（开放疑点/豁免/待核对数） |

## 五、纪律与常见坑

1. **手改了已基线文件**：`lint` 会报"原地修订"。想保留改动？先从 `design/store/` 按哈希恢复工作视图，再走 CP edit——不要试图绕过。
2. **批准后改了 CP**：哈希校验失败，必须重新 `cp hash` 呈报、重新批准。这是特性不是 bug。
3. **一个根因一个 CP**：别把无关改动塞进同一提案，否则批准粒度和追溯都失真。
4. **find 必须唯一命中**：edit 的 find 片段不唯一时 validate 会拒绝，加长上下文即可。
5. **新事实先入 inbox**：生成文档时冒出的新角色/新规则，进 inbox 走 CP 入 facts，绝不直接写进散文。
6. **别做全量语义审查**：L2 只核对 pending 里的配对；想"再全局刷一遍"的冲动，正是这套机制要消灭的成本。

建议第一次拿一个 3 份文档的小项目把 Step 0–6 完整跑通（半小时以内），验证"手改被拦、修订传播、一次批准多文件生效"三个关键行为，再上真实项目。
