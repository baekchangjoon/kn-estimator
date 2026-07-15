import json, re, sys
from pathlib import Path

MAP_RE = re.compile(
    r'@(Request|Get|Post|Put|Delete|Patch)Mapping\s*(?:\(([^)]*)\))?', re.S)
METHOD_OF = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH"}

def _attr(args, name):
    if not args:
        return None
    m = re.search(name + r'\s*=\s*"([^"]*)"', args)
    if m:
        return m.group(1)
    if name == "value":
        m = re.search(r'^\s*"([^"]*)"', args)
        return m.group(1) if m else None
    return None

DECL_RE = re.compile(r'public\s+[\w<>,\[\]. ]+\s+(\w+)\s*\(')

def _methods(src, class_pos):
    """클래스 선언 이후를 순회하며 (직전 어노테이션 블록, 핸들러명, 메서드 본문)을 산출.

    어노테이션은 줄 시작('@')부터 괄호 균형이 맞을 때까지 누적하고, public 메서드
    선언을 만나면 중괄호 균형으로 본문 끝까지 취한다 (문자열 리터럴 내 중괄호는
    이 코드베이스에서 균형을 깨지 않는 수준이라 허용 오차로 둔다).
    """
    lines = src[class_pos:].splitlines()
    pending, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("@"):
            ann = line
            while ann.count("(") > ann.count(")") and i + 1 < len(lines):
                i += 1
                ann += "\n" + lines[i]
            pending.append(ann)
            i += 1
            continue
        m = DECL_RE.search(line)
        if m and "class " not in line:
            body_lines, depth, started = [line], 0, False
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if j > i:
                    body_lines.append(lines[j])
                if started and depth <= 0:
                    break
                j += 1
            yield "\n".join(pending), m.group(1), "\n".join(body_lines)
            pending = []
            i = j + 1
            continue
        if stripped and not stripped.startswith("//") and not stripped.startswith("*"):
            pending = []
        i += 1

def scan(root):
    out = []
    for f in sorted(Path(root, "src/main/java").rglob("*Controller.java")):
        src = f.read_text(encoding="utf-8", errors="replace")
        if "@Controller" not in src and "@RestController" not in src:
            continue
        rest_class = "@RestController" in src
        cd = re.search(r'public\s+class\s+\w+', src)
        if not cd:
            continue
        head = src[:cd.start()]
        cm = re.search(r'@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?"([^"]*)"', head)
        base = cm.group(1) if cm else ""
        cls = f.stem
        for anns, handler, body in _methods(src, cd.start()):
            mm = MAP_RE.search(anns)
            if not mm:
                continue
            kind, args = mm.group(1), mm.group(2)
            if kind == "Request":
                mth = re.search(r'RequestMethod\.(\w+)', args or "")
                method = mth.group(1) if mth else "GET"
            else:
                method = METHOD_OF[kind]
            sub = _attr(args, "value") or _attr(args, "path") or ""
            is_json = rest_class or "JSON_VIEW" in body or "@ResponseBody" in anns
            if not is_json:
                continue
            # Spring은 base와 sub를 항상 "/"로 조인한다 (2x2 실험에서 발견된 버그 수정:
            # "/web/admin/quota" + "groups" → "/web/admin/quota/groups")
            if sub and not sub.startswith("/") and base and not base.endswith("/"):
                sub = "/" + sub
            out.append({"controller": cls, "file": str(f.relative_to(root)),
                        "method": method, "path": (base + sub) or "/",
                        "handler": handler, "json": True})
    return out

N1 = {"path": "/web/super/admin/mngTerms", "method": "GET"}  # PR #17과 동일 (사전 등록)

def select_n8(inv, seed=42):
    import random
    rng = random.Random(seed)
    pool = [e for e in inv if not (e["path"] == N1["path"] and e["method"] == N1["method"])]
    by_ctrl = {}
    for e in sorted(pool, key=lambda x: (x["controller"], x["path"], x["method"])):
        by_ctrl.setdefault(e["controller"], []).append(e)
    ctrls = sorted(by_ctrl)
    rng.shuffle(ctrls)
    picked, methods_seen = [], set()
    for want_new_method in (True, False):
        for c in ctrls:
            if len(picked) == 8:
                break
            if any(p["controller"] == c for p in picked):
                continue
            cands = [e for e in by_ctrl[c] if not want_new_method or e["method"] not in methods_seen]
            if not cands:
                continue
            e = rng.choice(cands)
            picked.append(e)
            methods_seen.add(e["method"])
    assert len(picked) == 8, f"N=8 selection shortfall: {len(picked)} (controllers with JSON endpoints: {len(by_ctrl)})"
    return sorted(picked, key=lambda x: (x["controller"], x["path"]))

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "legacy-sut")
    inv = scan(root)
    n1 = [e for e in inv if e["path"] == N1["path"] and e["method"] == N1["method"]]
    data = {"inventory_count": len(inv), "n1": n1, "n8": select_n8(inv)}
    results = Path(__file__).resolve().parent.parent / "results"
    results.mkdir(exist_ok=True)
    (results / "endpoints.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    lines = ["# LegacySut REST 엔드포인트 인벤토리 (사전 등록)", "",
             f"- 전체 JSON 엔드포인트: {len(inv)}개", f"- N=1: `{N1['method']} {N1['path']}` (PR #17 동일)",
             "", "## N=8 선정 (seed=42, 컨트롤러 중복 없음)", "",
             "| Controller | Method | Path | Handler |", "|---|---|---|---|"]
    for e in data["n8"]:
        lines.append(f"| {e['controller']} | {e['method']} | {e['path']} | {e['handler']} |")
    (results / "endpoint-inventory.md").write_text("\n".join(lines) + "\n")
    print(f"inventory={len(inv)} n1={len(n1)} n8={len(data['n8'])}")
