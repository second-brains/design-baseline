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

## manifest.json（唯一可变的指针文件）
`artifacts[rel].current` = 当前版本号；`versions[n] = {hash, parent, cp, baselined_at}`。
`coverage[] = {up, down, min}`：选择器为 ID 前缀（大写）或 doc type（小写）。

## CP（design/proposals/CP-xxxx.yaml）
`id / root_cause / facts_changed[] / patches[]`；patch：
- `action: edit` + `edits: [{find, replace, count?}]`——find 须在目标文件中命中 count 次（默认 1），否则拒绝应用。
- `action: create` + `content: <仓库相对路径>`——目标不得已存在。
路径相对 `design/`。同一文件不得出现多个 patch。**CP 一经批准即不可改动**（哈希校验）。

## 批准凭证（design/proposals/CP-xxxx.approval.json）
`{cp, cp_hash, approved_by, approved_at, statement}`，由**用户**填写；`db.py cp hash` 生成骨架。

## ledger.jsonl（append-only，CI 可写）
按 `(kind, key)` 取最后一条为当前状态：
| kind | key | 载荷 |
|---|---|---|
| baselined | 文件相对路径 | version, cp, approval |
| propagated | 文件相对路径 | version（已传播到的版本）|
| suspect | `up\|down` | status open/closed, root, reason |
| verdict | `up@hash\|down@hash` | verdict consistent/drift, evidence[] |
| waiver | `up\|down` 或 `coverage\|up` | owner, expires, reason |
| cp_applied | CP id | patches[] |