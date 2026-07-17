"""정적 스캔: 엔드포인트 인벤토리(N) + 엔드포인트별 작업량 슬라이스(w_i).

설계 v2.2 (리뷰 K3 반영):
- EP 단위 w에는 핸들러 메서드 span만 (컨트롤러 본체를 EP마다 중복 가산하지 않는다).
  컨트롤러 본체 토큰은 controller_shared_tokens로 **보고만** 한다 — 비용 모델은 이를
  가산하지 않는다. 컨트롤러를 읽는 비용은 캘리브레이션 계수(delta_ep/delta_env)에 이미
  포함돼 있고, 모델에는 절대 토큰 항 자체가 없다 (w는 상대 공변량으로만 쓰인다).
- 주입 해석: @Autowired 필드 / 생성자 파라미터 / Lombok private final 필드.
- 인터페이스 타입은 동일 트리 *Impl 폴백.
- MyBatis: DAO/Mapper가 참조하는 네임스페이스·패키지 병치 XML 조인.
- JPA: Spring Data 리포지토리의 extends 제네릭 첫 인자(엔티티)를 1-hop 조인.
- 매칭 실패는 unresolved 플래그 (조용한 0 금지).
"""
import re
from pathlib import Path

from . import endpoints as _ep

TOKENS_PER_BYTE = 1 / 4  # fallback 고지 대상 (보고서에 명시)
SPRING_INFRA = {  # 프레임워크 타입은 슬라이스에서 제외
    "DataSourceTransactionManager", "PlatformTransactionManager", "ObjectMapper",
    "RestTemplate", "WebClient", "HttpSession", "HttpServletRequest", "HttpServletResponse",
    "Model", "Logger", "MessageSource", "Environment",
}
EXTERNAL_CALL_TYPES = {"RestTemplate", "WebClient"}
MAX_DEPTH = 2               # 주입 그래프 탐색 상한
DEPTH_DECAY = {1: 1.0, 2: 0.5}  # 최단 깊이별 가중치 감쇠

FIELD_INJ_RE = re.compile(
    r"(?:@Autowired\s+(?:private|protected|public)?\s*|private\s+final\s+)"
    r"([A-Z]\w+)(?:<[^>]*>)?\s+\w+\s*;")
CTOR_PARAM_RE = re.compile(r"public\s+\w+\s*\(([^)]*)\)\s*\{")
TYPE_IN_PARAM_RE = re.compile(r"([A-Z]\w+)(?:<[^>]*>)?\s+\w+")
MAPPER_NS_RE = re.compile(r'<mapper\s+namespace\s*=\s*"([^"]+)"')
# DAO가 참조하는 statement ID의 네임스페이스. 두 관용구를 모두 잡는다:
#   레거시 sqlSession: selectOne("mngTerms.getFoo", p)  → "mngTerms"
#   MyBatis mapper 인터페이스: namespace가 FQCN         → 아래 _namespaces_of가 별도 처리
STATEMENT_ID_RE = re.compile(r'"([\w.]+)\.\w+"')
PACKAGE_RE = re.compile(r"package\s+([\w.]+)\s*;")
# Spring Data 리포지토리: extends <Base>Repository<Entity, ...>의 첫 제네릭 인자가 엔티티.
SPRING_DATA_EXTENDS_RE = re.compile(
    r"\binterface\s+\w+[^{]*?\bextends\b[^{]*?\b\w*Repository\s*<\s*(\w+)")


def tokens_of(path):
    try:
        return int(path.stat().st_size * TOKENS_PER_BYTE)
    except OSError:
        return 0


def inventory(root):
    return _ep.scan(root)


class _Index:
    """클래스명 → 소스 파일, 그리고 MyBatis XML 인덱스."""

    def __init__(self, root):
        self.root = Path(root)
        # glob 순서는 파일시스템 readdir에 의존한다. by_class는 선착순(setdefault)이라
        # 동일 stem 충돌 시, xmls는 files 순서에 각각 영향을 주므로 정렬로 고정한다.
        self.by_class = {}
        for f in sorted(self.root.glob("src/main/java/**/*.java")):
            self.by_class.setdefault(f.stem, f)
        self.xmls = sorted(self.root.glob("src/main/resources/**/*.xml"))
        # namespace → XML 파일들. 병치가 아닌 프로젝트에서도 조인하기 위해 필요하다.
        self.xml_by_namespace = {}
        for x in self.xmls:
            m = MAPPER_NS_RE.search(x.read_text(errors="replace"))
            if m:
                self.xml_by_namespace.setdefault(m.group(1), []).append(x)

    def resolve(self, type_name):
        """타입 → 파일. 인터페이스면 *Impl 폴백. (file, is_interface) 또는 None."""
        f = self.by_class.get(type_name)
        if f is None:
            return None
        src = f.read_text(errors="replace")
        if re.search(rf"\binterface\s+{type_name}\b", src):
            impl = self.by_class.get(type_name + "Impl")
            if impl is not None:
                return impl
        return f

    def mybatis_xml_for(self, java_file):
        """DAO/Mapper의 SQL XML — 네임스페이스 조인 우선, 패키지 병치 폴백.

        네임스페이스 조인은 XML을 공용 디렉토리에 모아두는 프로젝트를 지원한다. 두 관용구를
        모두 다룬다 — 레거시 sqlSession(`selectOne("ns.stmt")`처럼 statement ID 접두어가
        네임스페이스)과 mapper 인터페이스(네임스페이스가 FQCN). LegacySut는 전자다.

        네임스페이스로 아무것도 못 찾으면 기존 디렉토리 prefix 매칭으로 폴백한다 — 문자열
        상수가 아니라 다른 방식으로 SQL을 참조하는 코드가 있을 수 있다.
        """
        by_ns = []
        for ns in self._namespaces_of(java_file):
            by_ns.extend(self.xml_by_namespace.get(ns, []))
        if by_ns:
            return sorted(set(by_ns))
        return self._colocated_xml_for(java_file)

    def _namespaces_of(self, java_file):
        """이 DAO/Mapper가 참조하는 네임스페이스 후보."""
        src = java_file.read_text(errors="replace")
        out = set(STATEMENT_ID_RE.findall(src))          # 레거시: "ns.stmt" 접두어
        pkg = PACKAGE_RE.search(src)
        if pkg:
            out.add(f"{pkg.group(1)}.{java_file.stem}")  # mapper 인터페이스: FQCN
        return out

    def jpa_entity_for(self, java_file):
        """Spring Data 리포지토리의 엔티티 파일 — MyBatis XML 조인의 JPA 대응.

        `extends *Repository<Entity, ...>`의 첫 제네릭 인자를 해석한다. 미해석(엔티티가
        외부 모듈 등)은 XML 미발견과 동일하게 조용히 None — 리포지토리 자체는 해석에
        성공했으므로 unresolved가 아니다.
        """
        m = SPRING_DATA_EXTENDS_RE.search(java_file.read_text(errors="replace"))
        return self.by_class.get(m.group(1)) if m else None

    def _colocated_xml_for(self, java_file):
        out = []
        rel = java_file.parent.relative_to(self.root / "src/main/java").parent
        for x in self.xmls:
            try:
                xrel = x.relative_to(self.root / "src/main/resources")
            except ValueError:
                continue
            if str(xrel).startswith(str(rel)):
                out.append(x)
        return out


def _injected_types(src):
    """주입 타입을 정렬된 리스트로 반환.

    set을 그대로 돌려주면 순회 순서가 해시 랜덤화에 좌우돼 w_tokens가 실행마다 달라진다.
    """
    types = set(FIELD_INJ_RE.findall(src))
    for params in CTOR_PARAM_RE.findall(src):
        types.update(TYPE_IN_PARAM_RE.findall(params))
    # 외부 호출 타입은 SPRING_INFRA에도 들어 있지만 여기서 걸러내면 안 된다 — 걸러내면
    # build_slices의 external_call 분기가 도달 불가가 돼 플래그가 조용히 항상 False가 된다.
    # 이들은 build_slices에서 플래그만 세우고 토큰은 더하지 않는다.
    excluded = SPRING_INFRA - EXTERNAL_CALL_TYPES
    return sorted(t for t in types if t not in excluded
                  and not t.startswith(("String", "Long", "Int", "Map", "List")))


def _handler_span_tokens(controller_src, handler):
    cd = _ep.CLASS_DECL_RE.search(controller_src)
    for anns, name, body in _ep._methods(controller_src, cd.start() if cd else 0):
        if name == handler:
            return int(len((anns + body).encode()) * TOKENS_PER_BYTE)
    return 0


def build_slices(root, eps):
    root = Path(root)
    idx = _Index(root)
    out = []
    ctrl_cache = {}
    for e in eps:
        cf = root / e["file"]
        src = ctrl_cache.setdefault(e["file"], cf.read_text(errors="replace"))
        handler_tok = _handler_span_tokens(src, e["handler"])
        ctrl_tok = tokens_of(cf)
        files, unresolved, external = [str(cf.relative_to(root))], [], False
        seen = {e["controller"]}
        w = handler_tok

        # BFS: 감쇠는 엔드포인트로부터의 **최단 깊이**로 결정된다. DFS로 순회하면 공유
        # 의존 타입이 어느 부모에서 먼저 닿느냐에 따라 감쇠와 하위 탐색 범위가 갈렸다.
        frontier = [t for t in _injected_types(src) if t not in seen]
        seen.update(frontier)
        for depth in range(1, MAX_DEPTH + 1):
            if not frontier:
                break
            decay = DEPTH_DECAY[depth]
            next_frontier = []
            for type_name in frontier:
                if type_name in EXTERNAL_CALL_TYPES:
                    external = True
                    continue
                f = idx.resolve(type_name)
                if f is None:
                    if type_name.endswith(("Service", "DAO", "Dao", "Mapper", "Repository")):
                        unresolved.append(type_name)
                    continue
                w_add = int(tokens_of(f) * decay)
                files.append(str(f.relative_to(root)))
                fsrc = f.read_text(errors="replace")
                if type_name.endswith(("DAO", "Dao", "Mapper", "Repository")):
                    for x in idx.mybatis_xml_for(f):
                        files.append(str(x.relative_to(root)))
                        w_add += int(tokens_of(x) * decay)
                    ent = idx.jpa_entity_for(f)
                    if ent is not None and str(ent.relative_to(root)) not in files:
                        files.append(str(ent.relative_to(root)))
                        w_add += int(tokens_of(ent) * decay)
                w += w_add
                if depth < MAX_DEPTH:   # 마지막 깊이의 자식은 어차피 처리되지 않는다
                    for t in _injected_types(fsrc):
                        if t not in seen:
                            seen.add(t)
                            next_frontier.append(t)
            frontier = next_frontier

        out.append({"endpoint": e, "w_tokens": w, "handler_tokens": handler_tok,
                    "controller_shared_tokens": ctrl_tok, "files": files,
                    "unresolved": unresolved, "external_call": external})
    # 미해결 prior: 프로젝트 중앙값 w로 보정 (조용한 0 방지)
    resolved_ws = [s["w_tokens"] for s in out if not s["unresolved"]]
    med = sorted(resolved_ws)[len(resolved_ws) // 2] if resolved_ws else 0
    for s in out:
        if s["unresolved"] and s["w_tokens"] < med:
            s["w_prior_applied"] = True
            s["w_tokens"] = med
    return out


def w_hats(slices):
    ws = [s["w_tokens"] for s in slices]
    mean = sum(ws) / len(ws) if ws else 1
    return [w / mean for w in ws]


if __name__ == "__main__":
    import json, sys
    eps = inventory(sys.argv[1])
    sls = build_slices(sys.argv[1], eps)
    print(json.dumps({"n": len(eps),
                      "w_median": sorted(s["w_tokens"] for s in sls)[len(sls) // 2],
                      "unresolved_ratio": sum(1 for s in sls if s["unresolved"]) / len(sls)},
                     indent=2))
