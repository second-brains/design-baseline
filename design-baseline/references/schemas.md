# 字段定义

## 事实条目（design/facts/*.yaml）
顶层键为事实 ID（`^[A-Z][A-Z0-9]*-\d+$`），值为映射，必填 `summary: str`，其余自由扩展。
约定前缀：REQ 需求 / ROLE 角色 / ENT|FIELD 实体字段 / RULE 规则。ID 永不复用。

## 文档 front-matter（design/docs/**/*.md）
| 字段 | 必填 | 说明 |
|---|---|---|
| id | 是 | 唯一 ID，一文件一元素（PRD→PRD-n，故事→US-n，线框→SCR-n）|
| type | 是 | manifest.doc_types 之一 |
| based_on | 是（可空列表）| 上游引用，**必须钉版本** `ID@vN` |
| uses_facts | 是（可空列表）| 事实引用，**裸 ID** |
| omits | 是（可空列表）| `{fact: ID, reason: str}` |

## manifest.json（版本指针与门禁声明）
`artifacts[rel].current` = 当前版本号；`versions[n] = {hash, parent, cp, baselined_at}`。
`coverage[] = {up, down, min}`：选择器为 ID 前缀（大写）或 doc type（小写）；`min` 必须是正整数，统计不同的下游节点。覆盖规则为空、选择器未匹配对象、artifact 尚未传播到当前版本或影响未传播，都按未知/失败处理。

`checks[] = {id, description, mode, command?, inputs?, method?, timeout?, manual?}` 是显式全局约束。`db.py check add` 新建 `observe` 检查；先收集检查证据，再用 `check enforce` 升级。`command` 是 argv 数组，`inputs` 列出测试程序和数据文件/目录，`method` 选择 `e2e`/`property`/`differential`，`timeout` 范围为 1–300 秒；`manual: true` 表示人工语义复核，不能替代配置为可执行命令的检查。observe 的 fail/unknown 会显示 WARN，绝不显示为 PASS；enforce 只有当前模式下的 pass 才通过。

## CP（design/proposals/CP-xxxx.yaml）
`id / root_cause / facts_changed[] / patches[]`；patch：
- `action: edit` + `edits: [{find, replace, count?}]`——find 须在目标文件中命中 count 次（默认 1），否则拒绝应用。
- `action: create` + `content: <仓库相对路径>`——目标不得已存在。
路径相对 `design/`。同一文件不得出现多个 patch。**CP 一经批准即不可改动**（哈希校验）。

## 批准凭证（design/proposals/CP-xxxx.approval.json）
`{cp, cp_hash, approval_scope, approved_by, approved_at, statement}`，由**用户**填写；`db.py cp hash` 生成骨架。`cp_hash` 覆盖 CP YAML、edit 目标源文件哈希、create 输入哈希和每个实际输出 payload 哈希。apply 会重算全部值；兼容旧版仅哈希 CP YAML 的凭证会被拒绝，必须重新批准。

## ledger.jsonl（append-only，CI 可写）
按 `(kind, key)` 取最后一条为当前状态：
| kind | key | 载荷 |
|---|---|---|
| baselined | 文件相对路径 | version, cp, approval |
| propagated | 文件相对路径 | version（已传播到的版本）|
| suspect | `up\|down` | status open/closed, root, reason |
| verdict | `up@hash\|down@hash` | verdict consistent/drift, evidence[] |
| waiver | `up\|down` 或 `coverage\|up` | owner, expires, reason |
| graph_snapshot | `current` | edges[]；首次升级时从旧 `trace/graph.jsonl` 迁移 |
| verification | check ID | status, method/scenario, artifact heads, checker/environment/input/output hashes, evidence |
| policy_tamper | check ID | status open/closed, definition, reason；后续同定义成功执行或带原因人工处置可核销 |
| cp_applied | CP id | patches[] |

## 验证证据与边界
`verify run` 以 `shell=False` 执行声明的 argv，记录退出码和日志哈希。验证记录绑定全部当前 artifact head、manifest 中该检查的定义（含模式）、运行中 `db.py`、命令可执行文件、Python/平台/PyYAML/locale 环境、声明输入文件或目录哈希及输出日志。manual 记录可通过 `--evidence-file` 对本地证据报告做哈希绑定。任一绑定值变化即变为 unknown。为保守起见，任一 artifact 版本变化会使所有检查证据失效；当前不做精确影响闭包失效。

runner 仅传递 PATH、DB_ROOT 和 locale 环境变量，不传递其他进程环境变量。若被执行命令改写 manifest，runner 恢复执行前字节并写入持久 tamper 状态；可通过相同检查定义的后续成功执行或 `check resolve --reason ...` 记录人工处置。该机制防止检查子进程自改门禁；它不是签名或身份认证。用户仍需通过仓库权限、分支保护和 CI 审查保护 manifest、ledger 与测试输入；有仓库写权限者仍可伪造本地 ledger 或直接删改检查声明。本轮不声称密码学防伪或绝对完整。
