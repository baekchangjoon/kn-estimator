"""외부 프로젝트 일반화 시험(2026-07-17)에서 확인된 인벤토리 재현율 결함(B1/B2)과
JPA 엔티티 1-hop 확장의 TDD 고정 테스트. 합성 픽스처만 사용한다 — SUT 불필요.

근거: docs/superpowers/specs/2026-07-17-inventory-recall-jpa-1hop-design.md
"""
import textwrap
from pathlib import Path

from kn_estimator import endpoints, scan


def _project(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return str(tmp_path)


# ---- B1: 와일드카드 제네릭 반환 타입 ----------------------------------------

def test_wildcard_generic_return_type_is_scanned(tmp_path):
    """B1: `ResponseEntity<?>` 반환 핸들러가 조용히 누락되면 안 된다
    (실측: petclinic AuthController 2 EP 누락)."""
    root = _project(tmp_path, {
        "src/main/java/com/x/AuthController.java": """\
            package com.x;

            @RestController
            @RequestMapping("/api/auth")
            public class AuthController {

                @PostMapping("/login")
                public ResponseEntity<?> login(@RequestBody LoginRequest req) {
                    return ResponseEntity.ok().build();
                }

                @PostMapping("/plain")
                public ResponseEntity<TokenInfo> plain(@RequestBody TokenInfo req) {
                    return ResponseEntity.ok(req);
                }
            }
        """})
    got = {(e["method"], e["path"]) for e in endpoints.scan(root)}
    assert got == {("POST", "/api/auth/login"), ("POST", "/api/auth/plain")}


# ---- B2: 인라인 @ResponseBody + 배열형 매핑 값 -------------------------------

_MVC_CONTROLLER = """\
    package com.x;

    @Controller
    class VetController {

        @GetMapping({ "/vets" })
        public @ResponseBody Vets vetsJson() {
            return new Vets();
        }

        @GetMapping("/vets.html")
        public String vetsPage(Model model) {
            return "vets/vetList";
        }
    }
"""


def test_inline_response_body_and_array_mapping(tmp_path):
    """B2: 반환 타입 위치의 `@ResponseBody`와 배열형 `@GetMapping({ "/vets" })`,
    그리고 package-private 컨트롤러 클래스 (실측: petclinic VetController 1 EP 누락 —
    upstream petclinic 계열 컨트롤러는 전부 package-private이다).
    뷰 반환 MVC 핸들러는 계속 배제."""
    root = _project(tmp_path, {
        "src/main/java/com/x/VetController.java": _MVC_CONTROLLER})
    eps = endpoints.scan(root)
    assert [(e["method"], e["path"], e["handler"]) for e in eps] == \
        [("GET", "/vets", "vetsJson")]


def test_package_private_controller_handler_span_is_measured(tmp_path):
    """package-private 클래스에서도 핸들러 span이 측정돼야 한다 —
    scan.py가 'public class' 문자열 탐색으로 클래스 시작을 찾으면 0이 된다."""
    root = _project(tmp_path, {
        "src/main/java/com/x/VetController.java": _MVC_CONTROLLER})
    sls = scan.build_slices(root, endpoints.scan(root))
    assert len(sls) == 1
    assert sls[0]["handler_tokens"] > 0


# ---- JPA: 엔티티 1-hop (MyBatis XML 조인과 대칭) ------------------------------

_JPA_CONTROLLER = """\
    package com.x.web;

    @RestController
    @RequestMapping("/api/owners")
    public class OwnerController {

        private final OwnerRepository owners;

        @GetMapping("/{id}")
        public Owner one(@PathVariable int id) {
            return owners.findById(id);
        }
    }
"""

_JPA_REPOSITORY = """\
    package com.x.repo;

    public interface OwnerRepository extends JpaRepository<Owner, Integer> {
        Owner findById(int id);
    }
"""

_JPA_ENTITY = """\
    package com.x.domain;

    @Entity
    public class Owner {
        private Integer id;
        private String firstName;
        private String lastName;
        private String address;
        private String telephone;
    }
"""


def test_jpa_entity_joined_one_hop_at_repository_decay(tmp_path):
    """JPA: `extends *Repository<Entity, ID>`의 엔티티가 리포지토리와 같은 감쇠로
    w에 가산되고 files에 나타난다 (설계 결정: MyBatis XML 조인과 대칭)."""
    root = _project(tmp_path, {
        "src/main/java/com/x/web/OwnerController.java": _JPA_CONTROLLER,
        "src/main/java/com/x/repo/OwnerRepository.java": _JPA_REPOSITORY,
        "src/main/java/com/x/domain/Owner.java": _JPA_ENTITY,
    })
    sls = scan.build_slices(root, endpoints.scan(root))
    assert len(sls) == 1
    s = sls[0]
    assert "src/main/java/com/x/domain/Owner.java" in s["files"]
    repo_tok = scan.tokens_of(Path(root) / "src/main/java/com/x/repo/OwnerRepository.java")
    ent_tok = scan.tokens_of(Path(root) / "src/main/java/com/x/domain/Owner.java")
    # 깊이 1 → 감쇠 1.0이 리포지토리·엔티티에 동일 적용
    assert s["w_tokens"] == s["handler_tokens"] + repo_tok + ent_tok
    assert s["unresolved"] == []


def test_jpa_entity_missing_is_silently_skipped(tmp_path):
    """엔티티가 저장소 밖(외부 모듈 등)이면 크래시 없이 생략 — MyBatis XML 미발견과
    동일 거동. unresolved는 건드리지 않는다."""
    root = _project(tmp_path, {
        "src/main/java/com/x/web/OwnerController.java": _JPA_CONTROLLER,
        "src/main/java/com/x/repo/OwnerRepository.java": _JPA_REPOSITORY,
    })
    sls = scan.build_slices(root, endpoints.scan(root))
    s = sls[0]
    assert all(not f.endswith("Owner.java") for f in s["files"])
    repo_tok = scan.tokens_of(Path(root) / "src/main/java/com/x/repo/OwnerRepository.java")
    assert s["w_tokens"] == s["handler_tokens"] + repo_tok
    assert s["unresolved"] == []


def test_jpa_shared_entity_counted_once_per_slice(tmp_path):
    """한 슬라이스에서 두 리포지토리가 같은 엔티티를 가리켜도 1회만 가산."""
    second_repo = """\
        package com.x.repo;

        public interface OwnerAuditRepository extends CrudRepository<Owner, Long> {
            Owner findLatest();
        }
    """
    controller = _JPA_CONTROLLER.replace(
        "private final OwnerRepository owners;",
        "private final OwnerRepository owners;\n"
        "    private final OwnerAuditRepository audits;")
    root = _project(tmp_path, {
        "src/main/java/com/x/web/OwnerController.java": controller,
        "src/main/java/com/x/repo/OwnerRepository.java": _JPA_REPOSITORY,
        "src/main/java/com/x/repo/OwnerAuditRepository.java": second_repo,
        "src/main/java/com/x/domain/Owner.java": _JPA_ENTITY,
    })
    sls = scan.build_slices(root, endpoints.scan(root))
    s = sls[0]
    assert s["files"].count("src/main/java/com/x/domain/Owner.java") == 1
