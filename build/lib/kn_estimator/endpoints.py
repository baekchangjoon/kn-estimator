"""Spring 컨트롤러 정적 스캐너 — JSON 엔드포인트 인벤토리.

reduce-token 실험 저장소의 `harness/endpoints.py`에서 vendored (2026-07-16).
estimator가 쓰는 `scan()`과 `_methods()`만 가져왔다. 원본의 `N1`/`select_n8`/`__main__`은
그 실험의 N=8 표본 선정·사전등록 산출물이라 이 도구와 무관해 제외했다.
"""
import re
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
