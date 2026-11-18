# 本轮范围：全局一致性与验证

本轮在 design-baseline 现有机制上做增量设计收敛：沿用 CP → 人工批准 → 应用 → 传播 → L2 成对核对 → gate，不另建系统，也不替换现有数据结构。以下记录本轮目标、实现状态和证据边界。

## 目标

- 登记设计对象覆盖清单，并能从变更对象沿新旧依赖图计算影响闭包；把跨模块全局约束纳入检查。
- 未知依赖、未登记对象或未评估影响必须显式暴露，不能作为通过依据。完整性结论只对声明范围及其证据负责，不承诺绝对完备。
- 为检查器增加 Claude verification 方法：按场景选择真实端到端运行、属性测试或差分测试；批准凭证绑定实际批准的 payload 与源哈希；验证结果绑定被测版本、验证器版本和环境；相关内容或依赖变化时使结果失效。
- runner 执行中的验证规则不得由被测命令自行削弱。新检查先以 observe 模式运行，并用漏检回归案例和正常样本检验检查器，确认有效后再纳入 gate。

## 本轮实现状态

- 已实现：批准哈希绑定 CP YAML、edit 目标源、create 输入和输出 payload；apply 重算并拒绝任何变化。
- 已实现：保存旧/新追溯图；首次关系、加边、删边均进入 pair suspect 与 L2 pending；旧版 `trace/graph.jsonl` 在 gate 重建前迁移，包括已知空图。gate 检查 artifact 已传播到当前版本，且关系图快照没有未评估差异。
- 已实现：覆盖规则要求非空声明、匹配对象和正整数最小值，按不同下游节点计数；跨对象语义约束用显式 check，unknown 不视为 pass。
- 已实现：命令型 check 以 observe 默认注册，实际 argv 执行、禁止 shell、记录退出码与日志哈希；证据绑定基线、检查定义/模式、checker/命令解释器、环境和输入文件/目录。manifest 被检查子进程改写时会恢复并阻断 gate。
- 回归证据：`python3 -m unittest discover -s design-baseline/tests -v` 覆盖旧漏洞、正常流程、旧图迁移和 runner 策略自修改。
- 边界：artifact 变化会保守地使所有检查证据失效，尚未做精确依赖失效。覆盖只约束已登记 facts/docs 及声明 selector，不发现仓库外对象。manual 是人工语义断言，不是可执行验证。仓库写权限者仍可直接修改 manifest/ledger；没有身份认证或签名，不承诺密码学防伪。

## 非目标

- 不新增上下文平台、UI 或通用项目管理能力。
- 不在本轮实现未来路线图中的可视化影响看板、上下文分片检索优化、loop 自动编排或平台化。
- 不另立存储模型；继续以现有 CP、approval、manifest、trace/graph、pending、ledger 和 gate 为基础迭代。

## 验收目标

- 从现有流程出发，文档能说明覆盖清单、影响闭包、全局约束及未知项如何映射到 CP、传播、L2 和 gate；未评估项不会被报告为通过。
- 验证场景能说明测试方法选择、批准凭证与源哈希绑定、结果与版本/验证器/环境绑定，以及变更后的失效规则。
- 新检查先 observe，并有历史漏检与正常样本的检查器验证证据；规则不能被被测对象修改为更宽松。
- 声明范围与证据边界清楚，不声称绝对完整；README、workflow、schema 和 Skill 说明当前实现及剩余边界。

## 依据

- 现有设计基线原则：[PRINCIPLES.md](design-baseline/assets/PRINCIPLES.md)
- 修订流程：[workflow-revise.md](design-baseline/references/workflow-revise.md)
- L2 成对核对：[l2-pair-check.md](design-baseline/references/l2-pair-check.md)
- Claude verification 参考文章：[How the Claude Code team thinks about verification](https://claude.dev/blog/how-the-claude-code-team-thinks-about-verification/)。
