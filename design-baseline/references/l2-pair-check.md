# L2 成对语义核对（唯一需要 LLM 的检查）

对 `trace/pending.jsonl` 每一行 `{up, down}` 独立执行，**一次只比一对**，禁止合并成全局审查。

## 输入
- 上游：仅该 ID 的事实条目或文档片段（按 ID 检索，不灌整篇）
- 下游：仅该文档的相关段落

## 提示词模板
你是设计一致性核对器。只回答下列封闭问题，逐条给出 verdict 与证据行号；
不得改写任何文档，不得推测未写明的内容，不得扩展检查范围。

上游（{up}）：<内容>
下游（{down}）：<内容>

Q1 下游对上游的复述是否存在事实性冲突？
Q2 上游的约束/枚举/边界在下游是否被违反？
Q3 下游是否遗漏上游的强制性要求（且未在 omits 中声明）？

输出 JSON：{"verdict":"consistent|drift","issues":[{"q":1,"evidence":"L23","detail":"..."}]}
无问题则 issues 为空。仅当三问皆无问题时 verdict=consistent。

## 写回
- consistent：`db.py verdict --up X --down Y --verdict consistent --evidence "L23:..."`
- drift：`db.py verdict --up X --down Y --verdict drift --evidence "..."` → 按根因新开 CP

结论按内容哈希缓存，同一配对内容不变则永不重查。