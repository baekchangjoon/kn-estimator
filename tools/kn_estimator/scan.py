"""정적 스캔: 엔드포인트 인벤토리(N) + 엔드포인트별 작업량 슬라이스(w_i).

설계 v2.2 (리뷰 K3 반영):
- 컨트롤러 파일 토큰은 controller_shared_tokens로 분리 (청크당 1회 가산),
  EP 단위 w에는 핸들러 메서드 span만.
- 주입 해석: @Autowired 필드 / 생성자 파라미터 / Lombok private final 필드.
- 인터페이스 타입은 동일 트리 *Impl 폴백.
- MyBatis: DAO/Mapper가 참조하는 네임스페이스·패키지 병치 XML 조인.
- 매칭 실패는 unresolved 플래그 (조용한 0 금지).
"""
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "harness"))
import endpoints as _ep  # 경로 조인 수정판 스캐너 재사용

TOKENS_PER_BYTE = 1 / 4  # fallback 고지 대상 (보고서에 명시)
SPRING_INFRA = {  # 프레임워크 타입은 슬라이스에서 제외
    "DataSourceTransactionManager", "PlatformTransactionManager", "ObjectMapper",
    "RestTemplate", "WebClient", "HttpSession", "HttpServletRequest", "HttpServletResponse",
    "Model", "Logger", "MessageSource", "Environment",
}
EXTERNAL_CALL_TYPES = {"RestTemplate", "WebClient"}

FIELD_INJ_RE = re.compile(
    r"(?:@Autowired\s+(?:private|protected|public)?\s*|private\s+final\s+)"
    r"([A-Z]\w+)(?:<[^>]*>)?\s+\w+\s*;")
CTOR_PARAM_RE = re.compile(r"public\s+\w+\s*\(([^)]*)\)\s*\{")
TYPE_IN_PARAM_RE = re.compile(r"([A-Z]\w+)(?:<[^>]*>)?\s+\w+")


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
        self.by_class = {}
        for f in self.root.glob("src/main/java/**/*.java"):
            self.by_class.setdefault(f.stem, f)
        self.xmls = list(self.root.glob("src/main/resources/**/*.xml"))

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
        """DAO/Mapper 파일의 패키지 병치 XML + 네임스페이스 일치 XML."""
        out = []
        pkg_dir = java_file.parent
        rel = pkg_dir.relative_to(self.root / "src/main/java").parent  # dao/ 상위 모듈 디렉토리
        for x in self.xmls:
            try:
                xrel = x.relative_to(self.root / "src/main/resources")
            except ValueError:
                continue
            if str(xrel).startswith(str(rel)):
                out.append(x)
        return out


def _injected_types(src):
    types = set(FIELD_INJ_RE.findall(src))
    for params in CTOR_PARAM_RE.findall(src):
        types.update(TYPE_IN_PARAM_RE.findall(params))
    return {t for t in types if t not in SPRING_INFRA and not t.startswith(("String", "Long", "Int", "Map", "List"))}


def _handler_span_tokens(controller_src, handler):
    for anns, name, body in _ep._methods(controller_src, controller_src.find("public class")):
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

        def visit(type_name, depth):
            nonlocal w, external
            if type_name in seen or depth > 2:
                return
            seen.add(type_name)
            if type_name in EXTERNAL_CALL_TYPES:
                external = True
                return
            f = idx.resolve(type_name)
            if f is None:
                if type_name.endswith(("Service", "DAO", "Dao", "Mapper", "Repository")):
                    unresolved.append(type_name)
                return
            decay = 1.0 if depth == 1 else 0.5
            w_add = int(tokens_of(f) * decay)
            files.append(str(f.relative_to(root)))
            fsrc = f.read_text(errors="replace")
            if type_name.endswith(("DAO", "Dao", "Mapper", "Repository")):
                for x in idx.mybatis_xml_for(f):
                    files.append(str(x.relative_to(root)))
                    w_add += int(tokens_of(x) * decay)
            globals()["_"] = None  # no-op to keep closure simple
            _accumulate(w_add)
            for t in _injected_types(fsrc):
                visit(t, depth + 1)

        def _accumulate(v):
            nonlocal w
            w += v

        for t in _injected_types(src):
            visit(t, 1)

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
    import json
    eps = inventory(sys.argv[1])
    sls = build_slices(sys.argv[1], eps)
    print(json.dumps({"n": len(eps),
                      "w_median": sorted(s["w_tokens"] for s in sls)[len(sls) // 2],
                      "unresolved_ratio": sum(1 for s in sls if s["unresolved"]) / len(sls)},
                     indent=2))
