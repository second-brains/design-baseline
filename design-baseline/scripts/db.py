#!/usr/bin/env python3
"""design-baseline 确定性核心：L0 lint / L1 graph / 疑点传播 / 变更提案。依赖 PyYAML。"""
import argparse, hashlib, json, os, re, sys
from datetime import datetime, date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少依赖：pip install pyyaml")

SKILL_VERSION = "1.0.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
ROOT = Path(os.environ.get("DB_ROOT", ".")).resolve()
D = ROOT / "design"
MAN, STORE = D / "manifest.json", D / "store"
LEDGER, GRAPH, PENDING = D / "trace/ledger.jsonl", D / "trace/graph.jsonl", D / "trace/pending.jsonl"
ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
PIN_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)@v(\d+)$")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)

ERRORS, WARNINGS = [], []
def err(m): ERRORS.append(m)
def warn(m): WARNINGS.append(m)
def sha(b): return hashlib.sha256(b).hexdigest()
def now(): return datetime.now().isoformat(timespec="seconds")
def jdump(o): return json.dumps(o, ensure_ascii=False, sort_keys=True)
def read(p): return Path(p).read_text(encoding="utf-8")

def awrite(p, text):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_name(p.name + ".tmp"); t.write_text(text, encoding="utf-8"); os.replace(t, p)

def man():
    if not MAN.exists(): sys.exit("未初始化：先运行 db.py init")
    return json.loads(read(MAN))
def save_man(m): awrite(MAN, json.dumps(m, ensure_ascii=False, indent=2) + "\n")

def blob_put(text):
    h = sha(text.encode()); p = STORE / h[:2] / (h + ".blob")
    if not p.exists(): awrite(p, text)
    return h
def blob_get(h):
    p = STORE / h[:2] / (h + ".blob")
    return read(p) if p.exists() else None

def fm(text):
    m = FM_RE.match(text)
    return (yaml.safe_load(m.group(1)) or {}, m.group(2)) if m else (None, text)

def parse_facts(text):
    data = yaml.safe_load(text) or {}
    return {k: v for k, v in data.items() if ID_RE.match(str(k))} if isinstance(data, dict) else {}
def ehash(e): return sha(jdump(e).encode())

def tpl(name):
    p = D / "templates" / name
    return read(p) if p.exists() else read(ASSETS / "templates" / name)

def lappend(rec):
    rec = dict(rec); rec["at"] = now(); LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f: f.write(jdump(rec) + "\n")
def lstate():
    st = {}
    if LEDGER.exists():
        for line in read(LEDGER).splitlines():
            if line.strip():
                r = json.loads(line); st[(r["kind"], r["key"])] = r
    return st
def waiver_ok(st, key):
    w = st.get(("waiver", key))
    return bool(w and w.get("expires", "") >= date.today().isoformat())

def collect():
    facts, owner, docs = {}, {}, {}
    for f in sorted((D / "facts").glob("*.yaml")):
        rel = str(f.relative_to(D))
        for k, v in parse_facts(read(f)).items():
            if k in facts: err(f"事实 ID 重复：{k}（{owner[k]} 与 {rel}）")
            facts[k], owner[k] = v, rel
    for f in sorted((D / "docs").rglob("*.md")):
        rel = str(f.relative_to(D)); meta, body = fm(read(f))
        if meta is None: err(f"{rel}: 缺少 front-matter"); continue
        i = str(meta.get("id") or "")
        if not ID_RE.match(i): err(f"{rel}: id 非法（{i}）"); continue
        if i in docs: err(f"文档 ID 重复：{i}")
        docs[i] = {"rel": rel, "meta": meta, "body": body}
    return facts, owner, docs

def edges_of(docs):
    out = []
    for did, d in docs.items():
        for ref in d["meta"].get("based_on") or []:
            m = PIN_RE.match(str(ref))
            if not m: err(f"{did}: based_on 必须钉住版本（形如 REQ-001@v1），收到 {ref}"); continue
            out.append((did, m.group(1), "based_on", int(m.group(2))))
        for ref in d["meta"].get("uses_facts") or []:
            if PIN_RE.match(str(ref)): err(f"{did}: uses_facts 写裸 ID，不钉版本，收到 {ref}"); continue
            out.append((did, str(ref), "uses_facts", None))
    return out

def node_hash(n, facts, docs):
    if n in facts: return ehash(facts[n])[:12]
    if n in docs: return sha(read(D / docs[n]["rel"]).encode())[:12]
    return "missing"

def report(a):
    for w in WARNINGS: print("WARN ", w)
    for e in ERRORS: print("ERROR", e)
    print(f"— {len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
    return 1 if ERRORS and not getattr(a, "advisory", False) else 0

# ---------- L0 ----------
def do_lint(m, facts, owner, docs):
    for k, v in facts.items():
        if not isinstance(v, dict) or not v.get("summary"): err(f"事实 {k}: 缺少 summary")
    for did, d in docs.items():
        meta = d["meta"]
        if meta.get("type") not in m.get("doc_types", []): err(f"{did}: type 非法（{meta.get('type')}）")
        for fld in ("based_on", "uses_facts", "omits"):
            if not isinstance(meta.get(fld), list): err(f"{did}: {fld} 必须是列表（可为空）")
        for o in meta.get("omits") or []:
            if not isinstance(o, dict) or not o.get("fact") or not o.get("reason"):
                err(f"{did}: omits 每项须含 fact 与 reason")
        for ref in meta.get("uses_facts") or []:
            if str(ref) not in facts: err(f"{did}: uses_facts 指向非事实 ID {ref}")
        for ref in meta.get("based_on") or []:
            t = str(ref).split("@")[0]
            if t not in facts and t not in docs: err(f"{did}: based_on 指向不存在的 ID {t}")
    edges_of(docs)
    for rel, art in m["artifacts"].items():
        cur = art["versions"][str(art["current"])]; p = D / rel
        if not p.exists(): err(f"{rel}: 工作视图缺失（基线 v{art['current']}）"); continue
        if sha(read(p).encode()) != cur["hash"]:
            err(f"{rel}: 原地修订！工作视图与基线 v{art['current']} 不一致，必须走 CP")
        for vn, v in art["versions"].items():
            b = blob_get(v["hash"])
            if b is None: err(f"{rel}@v{vn}: 版本对象缺失")
            elif sha(b.encode()) != v["hash"]: err(f"{rel}@v{vn}: 版本对象哈希不符（不可变对象被改动）")

# ---------- L1 ----------
def do_graph(m, facts, owner, docs):
    E = edges_of(docs); nodes = set(facts) | set(docs); adj = {}
    for down, up, kind, pin in E:
        if up not in nodes: err(f"{down} → {up}: 悬空引用"); continue
        adj.setdefault(up, []).append(down)
        if pin is not None:
            art = m["artifacts"].get(owner.get(up) or docs[up]["rel"])
            if not art: warn(f"{down}: 上游 {up} 尚未基线化，钉住 v{pin} 暂不可解析")
            elif str(pin) not in art["versions"]: err(f"{down}: 钉住的 {up}@v{pin} 不存在")
    color = {}
    def dfs(n, stack):
        color[n] = 1
        for d in adj.get(n, []):
            if color.get(d) == 1: err("追溯图存在环：" + " → ".join(stack + [d]))
            elif color.get(d) is None: dfs(d, stack + [d])
        color[n] = 2
    for n in sorted(nodes):
        if color.get(n) is None: dfs(n, [])
    st = lstate()
    def match(sel, n):
        if n in docs and docs[n]["meta"].get("type") == sel: return True
        return n.startswith(sel + "-")
    gaps = 0
    for rule in m.get("coverage", []):
        for u in [n for n in nodes if match(rule["up"], n)]:
            hits = [d for d in adj.get(u, []) if match(rule["down"], d)]
            if len(hits) < rule.get("min", 1) and not waiver_ok(st, f"coverage|{u}"):
                err(f"覆盖缺口：{u} 未被 {rule['down']} 覆盖（{len(hits)}/{rule.get('min',1)}）"); gaps += 1
    awrite(GRAPH, "".join(jdump({"up": u, "down": d, "kind": k, "pin": p}) + "\n" for d, u, k, p in E))
    print(f"图：节点 {len(nodes)}，边 {len(E)}，覆盖缺口 {gaps}")
    return adj

def cmd_lint(a):
    m = man(); do_lint(m, *collect()); return report(a)
def cmd_graph(a):
    m = man(); do_graph(m, *collect()); return report(a)

# ---------- 传播 ----------
def snap(m, rel, vn):
    art = m["artifacts"].get(rel)
    return blob_get(art["versions"][str(vn)]["hash"]) if art and str(vn) in art["versions"] else None

def cmd_propagate(a):
    m = man(); facts, owner, docs = collect(); E = edges_of(docs)
    adj = {}
    for d, u, k, p in E: adj.setdefault(u, []).append(d)
    st = lstate(); changed = []
    for rel, art in m["artifacts"].items():
        cur = art["current"]; prev = st.get(("propagated", rel))
        if prev and prev["version"] == cur: continue
        if not prev:
            lappend({"kind": "propagated", "key": rel, "version": cur}); continue
        old, new = snap(m, rel, prev["version"]), snap(m, rel, cur)
        if old is None or new is None: err(f"{rel}: 传播所需快照缺失"); continue
        if rel.startswith("facts/"):
            o, n = parse_facts(old), parse_facts(new)
            changed += [k for k in set(o) | set(n) if ehash(o.get(k)) != ehash(n.get(k))]
        elif old != new:
            om, _ = fm(old); nm, _ = fm(new)
            i = (nm or {}).get("id") or (om or {}).get("id")
            if i: changed.append(i)
        lappend({"kind": "propagated", "key": rel, "version": cur})
    opened = 0
    for root in changed:
        seen, q = set(), [root]
        while q:
            u = q.pop(0)
            for d in adj.get(u, []):
                if d in seen: continue
                seen.add(d); key = f"{u}|{d}"; r = st.get(("suspect", key))
                if not r or r.get("status") != "open":
                    lappend({"kind": "suspect", "key": key, "status": "open", "root": root,
                             "reason": f"上游 {u} 内容变更（根因 {root}）"}); opened += 1
                q.append(d)
    st = lstate(); pend = []
    for (kind, key), r in sorted(st.items()):
        if kind != "suspect" or r.get("status") != "open" or waiver_ok(st, key): continue
        u, d = key.split("|")
        vh, dh = node_hash(u, facts, docs), node_hash(d, facts, docs)
        v = st.get(("verdict", f"{u}@{vh}|{d}@{dh}"))
        if v and v["verdict"] == "consistent":
            lappend({"kind": "suspect", "key": key, "status": "closed", "reason": "命中既有核对结论"}); continue
        pend.append({"up": u, "down": d, "cached": v["verdict"] if v else None})
    awrite(PENDING, "".join(jdump(p) + "\n" for p in pend))
    print(f"变更元素 {len(changed)}，新开疑点 {opened}，待核对 {len(pend)} → design/trace/pending.jsonl")
    return report(a)

def cmd_verdict(a):
    facts, owner, docs = collect()
    uh, dh = node_hash(a.up, facts, docs), node_hash(a.down, facts, docs)
    lappend({"kind": "verdict", "key": f"{a.up}@{uh}|{a.down}@{dh}", "verdict": a.verdict,
             "evidence": a.evidence or [], "checker": a.checker})
    if a.verdict == "consistent":
        lappend({"kind": "suspect", "key": f"{a.up}|{a.down}", "status": "closed", "reason": "L2 核对通过"})
    print(f"已记录 {a.up} → {a.down}: {a.verdict}"); return 0

def cmd_waiver(a):
    if a.expires < date.today().isoformat(): sys.exit("expires 必须是未来日期（豁免须有期限）")
    key = f"coverage|{a.up}" if a.down == "coverage" else f"{a.up}|{a.down}"
    lappend({"kind": "waiver", "key": key, "owner": a.owner, "expires": a.expires, "reason": a.reason})
    print(f"已登记豁免 {key}，到期 {a.expires}"); return 0

# ---------- 变更提案 ----------
def cp_file(cid): return D / "proposals" / f"{cid}.yaml"
def cp_load(cid):
    p = cp_file(cid)
    if not p.exists(): sys.exit(f"提案不存在：{p}")
    return yaml.safe_load(read(p)) or {}, sha(read(p).encode())

def cp_validate(cp):
    if not cp.get("root_cause"): err("CP 缺少 root_cause（按根因批量，不可空泛）")
    ps = cp.get("patches") or []
    if not ps: err("CP patches 为空")
    seen = set()
    for p in ps:
        rel = p.get("path", "")
        if rel in seen: err(f"{rel}: 同一文件出现多个 patch，请合并")
        seen.add(rel)
        if not rel or rel.startswith("/") or ".." in Path(rel).parts: err(f"非法路径 {rel}"); continue
        t = D / rel; act = p.get("action")
        if act == "create":
            if t.exists(): err(f"{rel}: 已存在，不能 create（改用 edit）")
            if not (ROOT / p.get("content", "")).exists(): err(f"{rel}: content 文件不存在")
        elif act == "edit":
            if not t.exists(): err(f"{rel}: 不存在，不能 edit"); continue
            txt = read(t)
            for e in p.get("edits") or []:
                if "find" not in e or "replace" not in e: err(f"{rel}: edit 须含 find/replace"); continue
                c, want = txt.count(e["find"]), e.get("count", 1)
                if c != want: err(f"{rel}: find 命中 {c} 次，期望 {want}（须唯一可定位）")
        else: err(f"{rel}: action 必须是 create 或 edit")

def cmd_cp(a):
    if a.op == "new":
        p = cp_file(a.id)
        if p.exists(): sys.exit("提案已存在；提案不可原地修订，请新建编号")
        awrite(p, tpl("cp.yaml").replace("__ID__", a.id).replace("__ROOT_CAUSE__", a.root_cause or "待填写"))
        print(f"已创建 design/proposals/{a.id}.yaml；填写 patches 后运行 cp validate"); return 0
    cp, h = cp_load(a.id)
    if a.op == "hash":
        print(json.dumps({"cp": a.id, "cp_hash": h, "approved_by": "", "approved_at": "",
                          "statement": "我批准本提案全部 patches"}, ensure_ascii=False, indent=2))
        print(f"\n# 由用户填写 approved_by/at 后保存为 design/proposals/{a.id}.approval.json"); return 0
    cp_validate(cp)
    if a.op == "validate": return report(a)
    if ERRORS: return report(a)
    if not a.approval or not Path(a.approval).exists(): sys.exit("缺少批准凭证，禁止应用（原则 X.3）")
    ap = json.loads(read(a.approval))
    if ap.get("cp") != a.id: sys.exit("批准凭证与提案编号不符")
    if ap.get("cp_hash") != h: sys.exit("提案在批准后被改动，禁止应用；请重新呈报批准")
    if not ap.get("approved_by"): sys.exit("批准凭证缺少 approved_by")
    m = man(); staged = []
    for p in cp["patches"]:
        rel = p["path"]; t = D / rel
        if p["action"] == "create": new = read(ROOT / p["content"])
        else:
            new = read(t)
            for e in p["edits"]: new = new.replace(e["find"], e["replace"])
        staged.append((rel, new))
    for rel, new in staged:
        h2 = blob_put(new); awrite(D / rel, new)
        art = m["artifacts"].setdefault(rel, {"current": 0, "versions": {}})
        parent = art["current"] or None; vn = art["current"] + 1
        art["versions"][str(vn)] = {"hash": h2, "parent": parent, "cp": a.id, "baselined_at": now()}
        art["current"] = vn
        lappend({"kind": "baselined", "key": rel, "version": vn, "cp": a.id, "approval": str(a.approval)})
    save_man(m)  # 单次原子指针切换
    lappend({"kind": "cp_applied", "key": a.id, "patches": [r for r, _ in staged]})
    print(f"已应用 {a.id}：{len(staged)} 个文件产生新版本。下一步：db.py propagate"); return 0

# ---------- 门禁 / 状态 ----------
def cmd_gate(a):
    m = man(); facts, owner, docs = collect()
    do_lint(m, facts, owner, docs); do_graph(m, facts, owner, docs)
    st = lstate(); open_ = [k for (kind, k), r in st.items()
                           if kind == "suspect" and r.get("status") == "open" and not waiver_ok(st, k)]
    for k in open_: err(f"未核销疑点：{k}（需 verdict=consistent 或有效 waiver）")
    print(f"门禁：未核销疑点 {len(open_)}")
    return report(a)

def cmd_status(a):
    m = man(); st = lstate(); facts, owner, docs = collect()
    op = [k for (kind, k), r in st.items() if kind == "suspect" and r.get("status") == "open"]
    wv = [k for (kind, k), r in st.items() if kind == "waiver" and waiver_ok(st, k)]
    pd = len(read(PENDING).splitlines()) if PENDING.exists() else 0
    print(f"事实 {len(facts)}，文档 {len(docs)}，已基线文件 {len(m['artifacts'])}")
    print(f"开放疑点 {len(op)}，有效豁免 {len(wv)}，待核对 {pd}")
    for k in op: print("  suspect", k)
    return 0

# ---------- 初始化 ----------
def cmd_init(a):
    if not ASSETS.exists(): sys.exit("请从 Skill 目录运行 init（找不到 assets/）")
    added, skipped = [], []
    def place(src_rel, dst_rel, executable=False):
        dst = ROOT / dst_rel
        if dst.exists(): skipped.append(dst_rel); return
        if getattr(a, "dry_run", False): added.append(dst_rel + " (dry-run)"); return
        awrite(dst, read(ASSETS / src_rel))
        if executable: os.chmod(dst, 0o755)
        added.append(dst_rel)
    for f in ("requirements", "roles", "entities", "rules"):
        place(f"facts/{f}.yaml", f"design/facts/{f}.yaml")
    place("PRINCIPLES.md", "design/PRINCIPLES.md")
    place("templates/doc.md", "design/templates/doc.md")
    place("templates/cp.yaml", "design/templates/cp.yaml")
    place("hooks/pre-commit", ".git/hooks/pre-commit", True)
    place("ci/design-ci.yml", ".github/workflows/design-ci.yml")
    if not getattr(a, "dry_run", False):
        for sub in ("docs/prd", "docs/stories", "docs/wireframes", "trace", "proposals", "inbox", "store", "tools"):
            (D / sub).mkdir(parents=True, exist_ok=True)
            kp = D / sub / ".gitkeep"
            if not kp.exists(): awrite(kp, "")
        tool = D / "tools/db.py"
        if not tool.exists(): awrite(tool, read(Path(__file__))); os.chmod(tool, 0o755)
        if not MAN.exists():
            save_man({"schema": 1, "skill": {"name": "design-baseline", "version": SKILL_VERSION,
                                             "tool_hash": sha(read(tool).encode())},
                      "doc_types": ["prd", "story", "wireframe", "api", "test"],
                      "coverage": [{"up": "REQ", "down": "story", "min": 1},
                                   {"up": "story", "down": "SCR", "min": 1}],
                      "artifacts": {}})
        mn = man()
        if "PRINCIPLES.md" not in mn["artifacts"]:
            h = blob_put(read(D / "PRINCIPLES.md"))
            mn["artifacts"]["PRINCIPLES.md"] = {"current": 1, "versions": {
                "1": {"hash": h, "parent": None, "cp": "INIT", "baselined_at": now()}}}
            save_man(mn)
            lappend({"kind": "baselined", "key": "PRINCIPLES.md", "version": 1, "cp": "INIT",
                     "approval": "用户执行 init 即为授权"})
        ag = ROOT / "AGENTS.md"; snip = read(ASSETS / "AGENTS.snippet.md")
        if not ag.exists() or snip.strip() not in read(ag):
            awrite(ag, (read(ag) + "\n" if ag.exists() else "") + snip)
            added.append("AGENTS.md (append)")
    print("新增：", *added or ["(无)"], sep="\n  ")
    print("已存在跳过：", *skipped or ["(无)"], sep="\n  ")
    print("\n下一步：填写 design/facts/*.yaml → 撰写草稿 → cp new 基线化")
    return 0

def main():
    p = argparse.ArgumentParser(prog="db.py"); sub = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn):
        q = sub.add_parser(name); q.add_argument("--advisory", action="store_true"); q.set_defaults(fn=fn); return q
    add("init", cmd_init).add_argument("--dry-run", action="store_true")
    for n, f in (("lint", cmd_lint), ("graph", cmd_graph), ("propagate", cmd_propagate),
                 ("gate", cmd_gate), ("status", cmd_status)): add(n, f)
    v = add("verdict", cmd_verdict)
    v.add_argument("--up", required=True); v.add_argument("--down", required=True)
    v.add_argument("--verdict", required=True, choices=["consistent", "drift"])
    v.add_argument("--evidence", action="append"); v.add_argument("--checker", default="agent")
    w = add("waiver", cmd_waiver)
    w.add_argument("--up", required=True); w.add_argument("--down", required=True)
    w.add_argument("--owner", required=True); w.add_argument("--expires", required=True)
    w.add_argument("--reason", required=True)
    c = add("cp", cmd_cp)
    c.add_argument("op", choices=["new", "validate", "hash", "apply"])
    c.add_argument("--id", required=True); c.add_argument("--root-cause"); c.add_argument("--approval")
    a = p.parse_args(); sys.exit(a.fn(a) or 0)

if __name__ == "__main__":
    main()
