#!/usr/bin/env python3
"""design-baseline 确定性核心：L0 lint / L1 graph / 疑点传播 / 变更提案。依赖 PyYAML。"""
import argparse, hashlib, json, os, re, sys, shutil, subprocess
import platform
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

def awrite_bytes(p, data):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_name(p.name + ".tmp"); t.write_bytes(data); os.replace(t, p)

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

def file_hash(p): return sha(Path(p).read_bytes()) if Path(p).is_file() else None

def input_hash(p):
    p = Path(p)
    if p.is_file(): return file_hash(p)
    if p.is_dir():
        entries = {str(f.relative_to(p)): file_hash(f) for f in sorted(p.rglob("*")) if f.is_file()}
        return sha(jdump(entries).encode())
    return None

def artifact_fingerprint(m):
    return sha(jdump({rel: art["versions"][str(art["current"])]["hash"]
                      for rel, art in sorted(m.get("artifacts", {}).items())}).encode())

def checker_fingerprint(check=None):
    parts = {"db": file_hash(Path(__file__).resolve())}
    if check:
        command = check.get("command") or []
        executable = command[0] if command else None
        resolved = shutil.which(executable) if executable else None
        if executable and not resolved and (ROOT / executable).is_file(): resolved = str((ROOT / executable).resolve())
        parts["command"] = command
        parts["executable"] = file_hash(resolved) if resolved else None
    return sha(jdump(parts).encode())

def verification_environment():
    return {"python": platform.python_version(), "platform": platform.platform(),
            "pyyaml": getattr(yaml, "__version__", "unknown"),
            "path_hash": sha(os.environ.get("PATH", "").encode()),
            "lang": os.environ.get("LANG"), "lc_all": os.environ.get("LC_ALL"),
            "tz": os.environ.get("TZ")}

def verification_command_environment():
    env = {"PATH": os.environ.get("PATH", ""), "DB_ROOT": str(ROOT)}
    for key in ("LANG", "LC_ALL", "TZ"):
        if key in os.environ: env[key] = os.environ[key]
    return env

def verification_inputs(paths):
    result = {}
    for value in paths:
        p = (ROOT / value).resolve()
        if not p.is_relative_to(ROOT) or not (p.is_file() or p.is_dir()):
            err(f"验证输入不存在或越界：{value}"); continue
        result[str(p.relative_to(ROOT))] = input_hash(p)
    return result

def graph_edges(docs):
    return sorted({(d, u, k, p) for d, u, k, p in edges_of(docs)})

def seed_graph_snapshot():
    if ("graph_snapshot", "current") in lstate() or not GRAPH.is_file(): return
    edges = []
    for line in read(GRAPH).splitlines():
        if not line.strip(): continue
        edge = json.loads(line)
        edges.append([edge["down"], edge["up"], edge["kind"], edge.get("pin")])
    lappend({"kind": "graph_snapshot", "key": "current", "edges": edges, "legacy": True})

def prepare_graph_rebuild():
    had_legacy_file = GRAPH.is_file()
    seed_graph_snapshot()
    if not had_legacy_file and ("graph_snapshot", "current") not in lstate():
        lappend({"kind": "graph_snapshot", "key": "current", "edges": [], "initial": True})

def check_definition(check):
    return sha(jdump(check).encode())

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
        raw = yaml.safe_load(read(f)) or {}
        if isinstance(raw, dict):
            for key in raw:
                if not ID_RE.match(str(key)): err(f"{rel}: 未登记或非法事实键 {key}")
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
    rules = m.get("coverage", [])
    if not rules: err("未声明覆盖范围；空范围不能作为全局一致性通过")
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("up") or not rule.get("down"):
            err("coverage 每条规则须声明 up/down 选择器"); continue
        minimum = rule.get("min", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            err(f"覆盖规则 {rule['up']} → {rule['down']}: min 必须是正整数"); continue
        selected = [n for n in nodes if match(rule["up"], n)]
        if not selected: err(f"覆盖规则 {rule['up']} → {rule['down']}: 未匹配到任何对象，范围未知")
        for u in selected:
            hits = {d for d in adj.get(u, []) if match(rule["down"], d)}
            if len(hits) < minimum and not waiver_ok(st, f"coverage|{u}"):
                err(f"覆盖缺口：{u} 未被 {rule['down']} 覆盖（{len(hits)}/{minimum}）"); gaps += 1
    awrite(GRAPH, "".join(jdump({"up": u, "down": d, "kind": k, "pin": p}) + "\n" for d, u, k, p in E))
    print(f"图：节点 {len(nodes)}，边 {len(E)}，覆盖缺口 {gaps}")
    return adj

def cmd_lint(a):
    m = man(); do_lint(m, *collect()); return report(a)
def cmd_graph(a):
    prepare_graph_rebuild()
    m = man(); do_graph(m, *collect()); return report(a)

# ---------- 传播 ----------
def snap(m, rel, vn):
    art = m["artifacts"].get(rel)
    return blob_get(art["versions"][str(vn)]["hash"]) if art and str(vn) in art["versions"] else None

def cmd_propagate(a):
    seed_graph_snapshot()
    m = man(); facts, owner, docs = collect(); E = edges_of(docs)
    adj = {}
    for d, u, k, p in E: adj.setdefault(u, []).append(d)
    st = lstate(); changed = []
    old_graph = st.get(("graph_snapshot", "current"), {}).get("edges", [])
    new_graph = [list(e) for e in graph_edges(docs)]
    old_adj = {}
    for d, u, k, p in old_graph: old_adj.setdefault(u, []).append(d)
    old_set, new_set = {tuple(e) for e in old_graph}, {tuple(e) for e in new_graph}
    removed = old_set - new_set
    added = new_set - old_set
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
    for d, u, k, p in added:
        key = f"{u}|{d}"
        lappend({"kind": "suspect", "key": key, "status": "open", "root": u,
                 "reason": "追溯关系已加入新图，需核对新增引用影响"})
        opened += 1
    # Evaluate impact through both snapshots: deleted relationships still need review.
    for d, u, k, p in removed:
        key = f"{u}|{d}"
        lappend({"kind": "suspect", "key": key, "status": "open", "root": u,
                 "reason": "追溯关系已从新图删除，需核对删除影响"})
        opened += 1
    for root in changed:
        seen, q = set(), [root]
        while q:
            u = q.pop(0)
            for d in set(adj.get(u, [])) | set(old_adj.get(u, [])):
                if d in seen: continue
                seen.add(d); key = f"{u}|{d}"; r = st.get(("suspect", key))
                if not r or r.get("status") != "open":
                    lappend({"kind": "suspect", "key": key, "status": "open", "root": root,
                             "reason": f"上游 {u} 内容变更（根因 {root}）"}); opened += 1
                q.append(d)
    lappend({"kind": "graph_snapshot", "key": "current", "edges": new_graph})
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

def cmd_verify(a):
    m = man()
    check = next((c for c in m.get("checks", []) if c.get("id") == a.check), None)
    if not check: sys.exit(f"manifest 未声明检查：{a.check}")
    if check.get("command"): sys.exit("此检查配置了可执行命令，必须使用 verify run")
    if not check.get("manual"): sys.exit("此检查不是 manual 类型，不能记录人工结果")
    evidence_files = a.evidence_file or []
    inputs = verification_inputs(check.get("inputs", []) + (a.input or []) + evidence_files)
    if ERRORS: return report(a)
    if not a.evidence and not evidence_files: sys.exit("verification 必须记录文字证据或本地证据文件")
    record = {"kind": "verification", "key": a.check,
              "status": a.result, "method": a.method, "scenario": a.scenario,
              "definition": check_definition(check),
              "baseline": artifact_fingerprint(m), "checker": checker_fingerprint(check),
              "environment": verification_environment(), "inputs": inputs,
              "evidence": a.evidence or [], "evidence_files": evidence_files}
    lappend(record)
    print(f"已记录检查 {a.check}: {a.result}（{a.method}）")
    return 0

def cmd_verify_run(a):
    m = man()
    check = next((c for c in m.get("checks", []) if c.get("id") == a.check), None)
    if not check or not check.get("command"): sys.exit(f"没有可执行检查配置：{a.check}")
    inputs = verification_inputs(check.get("inputs", []))
    if ERRORS: return report(a)
    policy_before = MAN.read_bytes()
    returncode, stdout, stderr, command_error = 127, "", "", None
    try:
        result = subprocess.run(check["command"], cwd=ROOT, env=verification_command_environment(),
                                capture_output=True, text=True, shell=False,
                                timeout=check.get("timeout", 300))
        returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as ex:
        command_error = f"{type(ex).__name__}: {ex}"
        if isinstance(ex, subprocess.TimeoutExpired):
            returncode = 124
            stdout = ex.stdout.decode(errors="replace") if isinstance(ex.stdout, bytes) else ex.stdout or ""
            stderr = ex.stderr.decode(errors="replace") if isinstance(ex.stderr, bytes) else ex.stderr or ""
        stderr = (stderr or "") + "\n" + command_error
    finally:
        policy_changed = not MAN.is_file() or MAN.read_bytes() != policy_before
        if policy_changed: awrite_bytes(MAN, policy_before)
    rel = f"verification/logs/{a.check}.log"
    log = ("[stdout]\n" + stdout + "\n[stderr]\n" + stderr +
           f"\n[exit_code] {returncode}\n")
    awrite(D / rel, log)
    status = "pass" if returncode == 0 and not policy_changed else "fail"
    definition = check_definition(check)
    lappend({"kind": "verification", "key": a.check,
             "status": status,
             "method": check.get("method", "command"), "scenario": check.get("description", a.check),
             "definition": check_definition(check), "baseline": artifact_fingerprint(m),
             "checker": checker_fingerprint(check), "environment": verification_environment(),
             "inputs": inputs, "output": rel, "output_hash": file_hash(D / rel),
             "exit_code": returncode, "command_error": command_error})
    tamper_state = lstate().get(("policy_tamper", a.check))
    if policy_changed:
        lappend({"kind": "policy_tamper", "key": a.check, "status": "open",
                 "definition": definition, "reason": "被执行检查修改 manifest；已恢复执行前策略"})
    elif tamper_state and tamper_state.get("status") == "open" and tamper_state.get("definition") == definition and status == "pass":
        lappend({"kind": "policy_tamper", "key": a.check, "status": "closed",
                 "definition": definition, "reason": "策略未变且后续可执行检查通过"})
    print(f"已执行检查 {a.check}: {status}（日志 design/{rel}）")
    if stdout: print(stdout, end="")
    if stderr: print(stderr, file=sys.stderr, end="")
    return 0 if status == "pass" else 1

def cmd_check(a):
    m = man()
    checks = m.setdefault("checks", [])
    current = next((c for c in checks if c.get("id") == a.id), None)
    if a.op == "add":
        if current: sys.exit(f"检查已存在：{a.id}")
        if not ID_RE.match(a.id): sys.exit(f"检查 ID 非法：{a.id}")
        if not a.description: sys.exit("请说明全局检查的约束内容")
        if bool(a.command) == bool(a.manual): sys.exit("必须且只能选择 --command 或 --manual")
        if a.command and not a.input: sys.exit("可执行检查必须声明 --input 文件，以绑定测试代码和数据")
        if a.command and not a.method: sys.exit("可执行检查必须选择 e2e/property/differential 方法")
        if a.manual and a.method: sys.exit("manual 检查不接受可执行测试方法")
        if a.timeout < 1 or a.timeout > 300: sys.exit("timeout 必须在 1 到 300 秒之间")
        checks.append({"id": a.id, "description": a.description, "mode": "observe",
                       "command": a.command or [], "inputs": a.input or [], "manual": a.manual,
                       "method": a.method or "manual", "timeout": a.timeout})
        save_man(m)
        print(f"已登记检查 {a.id}，当前为 observe")
        return 0
    if not current: sys.exit(f"检查不存在：{a.id}")
    if a.op == "resolve":
        if not a.reason: sys.exit("resolve 必须附人工处置原因")
        tamper = lstate().get(("policy_tamper", a.id))
        if not tamper or tamper.get("status") != "open": sys.exit(f"没有待处置的策略篡改：{a.id}")
        lappend({"kind": "policy_tamper", "key": a.id, "status": "closed",
                 "definition": tamper.get("definition"), "reason": a.reason})
        print(f"已记录 {a.id} 的人工处置：{a.reason}")
        return 0
    if current.get("mode") == "enforce": sys.exit(f"检查已是 enforce：{a.id}")
    rec = lstate().get(("verification", a.id))
    status, why = check_freshness(current, rec, m)
    if status != "pass": sys.exit(f"检查无法升为 enforce：{status}（{why}）")
    current["mode"] = "enforce"
    save_man(m)
    print(f"已将检查 {a.id} 设为 enforce")
    return 0

def check_freshness(check, rec, m):
    if not rec: return "unknown", "无 verification 记录"
    if rec.get("definition") != check_definition(check): return "unknown", "检查定义已变化"
    if rec.get("baseline") != artifact_fingerprint(m): return "unknown", "被测基线已变化"
    if rec.get("checker") != checker_fingerprint(check): return "unknown", "检查器或被执行程序版本已变化"
    if rec.get("environment") != verification_environment(): return "unknown", "运行环境已变化"
    for rel, expected in rec.get("inputs", {}).items():
        if input_hash(ROOT / rel) != expected: return "unknown", f"验证输入已变化或缺失：{rel}"
    if check.get("command") and rec.get("method") != check.get("method"):
        return "unknown", "可执行检查方法或结果来源不符"
    if check.get("manual") and rec.get("method") != "manual": return "unknown", "manual 检查缺少人工复核记录"
    if rec.get("output") and file_hash(D / rec["output"]) != rec.get("output_hash"):
        return "unknown", "执行日志已变化或缺失"
    if rec.get("status") not in {"pass", "fail"}: return "unknown", "结果未评估"
    return rec["status"], f"{rec.get('method')} / {rec.get('scenario')}"

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
        if rel in {"manifest.json"} or rel.startswith(("store/", "trace/", "tools/", "verification/")):
            err(f"{rel}: CP 不得直接修改系统状态文件"); continue
        t = D / rel; act = p.get("action")
        if act == "create":
            if t.exists(): err(f"{rel}: 已存在，不能 create（改用 edit）")
            src = (ROOT / p.get("content", "")).resolve()
            if not src.is_relative_to(ROOT) or not src.is_file(): err(f"{rel}: content 文件不存在或越界")
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
        try: _, staged = cp_stage(cp)
        except (OSError, ValueError, KeyError) as ex: sys.exit(f"无法生成批准摘要：{ex}")
        payload = {"cp_yaml": h, "staged": staged}
        approval_hash = sha(jdump(payload).encode())
        print(json.dumps({"cp": a.id, "cp_hash": approval_hash, "approval_scope": payload,
                          "approved_by": "", "approved_at": "",
                          "statement": "我批准本提案全部 patches"}, ensure_ascii=False, indent=2))
        print(f"\n# 由用户填写 approved_by/at 后保存为 design/proposals/{a.id}.approval.json"); return 0
    cp_validate(cp)
    if a.op == "validate": return report(a)
    if ERRORS: return report(a)
    if not a.approval or not Path(a.approval).exists(): sys.exit("缺少批准凭证，禁止应用（原则 X.3）")
    ap = json.loads(read(a.approval))
    if ap.get("cp") != a.id: sys.exit("批准凭证与提案编号不符")
    staged, payload = cp_stage(cp)
    approval_hash = sha(jdump({"cp_yaml": h, "staged": payload}).encode())
    if ap.get("cp_hash") != approval_hash: sys.exit("提案、目标源文件或实际 payload 在批准后被改动，禁止应用；请重新呈报批准")
    if not ap.get("approved_by"): sys.exit("批准凭证缺少 approved_by")
    m = man(); old_contents = {rel: read(D / rel) if (D / rel).exists() else None for rel, _ in staged}
    for rel, new in staged: blob_put(new)
    baselined = []
    try:
      for rel, new in staged:
        h2 = sha(new.encode()); awrite(D / rel, new)
        art = m["artifacts"].setdefault(rel, {"current": 0, "versions": {}})
        parent = art["current"] or None; vn = art["current"] + 1
        art["versions"][str(vn)] = {"hash": h2, "parent": parent, "cp": a.id, "baselined_at": now()}
        art["current"] = vn
        baselined.append({"kind": "baselined", "key": rel, "version": vn,
                          "cp": a.id, "approval": str(a.approval)})
      save_man(m)  # 单次原子指针切换
    except Exception:
      for rel, old in old_contents.items():
        if old is None: (D / rel).unlink(missing_ok=True)
        else: awrite(D / rel, old)
      raise
    for record in baselined: lappend(record)
    lappend({"kind": "cp_applied", "key": a.id, "patches": [r for r, _ in staged]})
    print(f"已应用 {a.id}：{len(staged)} 个文件产生新版本。下一步：db.py propagate"); return 0

# ---------- 门禁 / 状态 ----------
def cmd_gate(a):
    prepare_graph_rebuild()
    m = man(); facts, owner, docs = collect()
    do_lint(m, facts, owner, docs); do_graph(m, facts, owner, docs)
    st = lstate(); open_ = [k for (kind, k), r in st.items()
                           if kind == "suspect" and r.get("status") == "open" and not waiver_ok(st, k)]
    for rel, art in m.get("artifacts", {}).items():
        propagated = st.get(("propagated", rel))
        if not propagated or propagated.get("version") != art.get("current"):
            err(f"{rel}: 尚未传播到当前版本；未知影响不得视为通过")
    for (kind, key), record in st.items():
        if kind == "policy_tamper" and record.get("status") == "open":
            err(f"检查 {key} 曾尝试修改受保护策略：必须重新运行成功检查或执行带原因的人工 resolve")
    for k in open_: err(f"未核销疑点：{k}（需 verdict=consistent 或有效 waiver）")
    current_edges = [list(e) for e in graph_edges(docs)]
    graph_record = st.get(("graph_snapshot", "current"))
    if not graph_record or graph_record.get("edges") != current_edges:
        err("依赖图尚未传播或传播快照已过期；未知影响不得视为通过")
    for check in m.get("checks", []):
        rec = st.get(("verification", check["id"]))
        status, why = check_freshness(check, rec, m)
        msg = f"检查 {check['id']} [{check.get('mode', 'observe')}]: {status}（{why}）"
        if check.get("mode") == "enforce":
            if status != "pass": err(msg)
            else: print("PASS ", msg)
        elif check.get("mode") == "observe":
            if status != "pass": warn(msg)
            else: print("PASS ", msg)
        else: err(f"检查 {check.get('id')}: mode 必须是 observe 或 enforce")
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

def cp_stage(cp):
    """Return exact proposed bytes and the source hashes bound by approval."""
    staged, payload = [], []
    for patch in cp.get("patches") or []:
        rel, target = patch["path"], D / patch["path"]
        source_hash = file_hash(target)
        if patch["action"] == "create":
            source = (ROOT / patch["content"]).resolve()
            if not source.is_relative_to(ROOT): raise ValueError(f"{rel}: create source 越界")
            new = read(source)
            source_ref = str(source.relative_to(ROOT))
        else:
            if source_hash is None: raise ValueError(f"{rel}: edit target 不存在")
            new = read(target)
            for edit in patch.get("edits") or []:
                if new.count(edit["find"]) != edit.get("count", 1):
                    raise ValueError(f"{rel}: edit find 命中数在批准后变化")
                new = new.replace(edit["find"], edit["replace"])
            source_ref = rel
        payload.append({"path": rel, "action": patch["action"], "source": source_ref,
                        "source_hash": source_hash if patch["action"] == "edit" else file_hash(source),
                        "payload_hash": sha(new.encode())})
        staged.append((rel, new))
    return staged, payload

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
    place("checks/global_coverage.py", "design/tools/global_coverage.py")
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
                      "checks": [],
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
    z = add("verify", lambda a: a.fn(a))
    zsub = z.add_subparsers(dest="verify_action", required=True)
    zm = zsub.add_parser("manual"); zm.set_defaults(fn=cmd_verify)
    zm.add_argument("--check", required=True); zm.add_argument("--result", choices=["pass", "fail", "unknown"], required=True)
    zm.add_argument("--method", choices=["manual"], default="manual")
    zm.add_argument("--scenario", required=True); zm.add_argument("--input", action="append")
    zm.add_argument("--evidence", action="append"); zm.add_argument("--evidence-file", action="append")
    zr = zsub.add_parser("run"); zr.set_defaults(fn=cmd_verify_run); zr.add_argument("--check", required=True)
    k = add("check", cmd_check)
    k.add_argument("op", choices=["add", "enforce", "resolve"]); k.add_argument("--id", required=True)
    k.add_argument("--description")
    k.add_argument("--reason")
    k.add_argument("--timeout", type=int, default=300)
    mode = k.add_mutually_exclusive_group(); mode.add_argument("--command", nargs="+"); mode.add_argument("--manual", action="store_true")
    k.add_argument("--input", action="append")
    k.add_argument("--method", choices=["e2e", "property", "differential"])
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
