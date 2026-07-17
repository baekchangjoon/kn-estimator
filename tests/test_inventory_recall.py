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

def test_inline_response_body_and_array_mapping(tmp_path):
    """B2: 반환 타입 위치의 `@ResponseBody`와 배열형 `@GetMapping({ "/vets" })`
    (실측: petclinic VetController 1 EP 누락). 뷰 반환 MVC 핸들러는 계속 배제."""
    root = _project(tmp_path, {
        "src/main/java/com/x/VetController.java": """\
            package com.x;

            @Controller
            public class VetController {

                @GetMapping({ "/vets" })
                public @ResponseBody Vets vetsJson() {
                    return new Vets();
                }

                @GetMapping("/vets.html")
                public String vetsPage(Model model) {
                    return "vets/vetList";
                }
            }
        """})
    eps = endpoints.scan(root)
    assert [(e["method"], e["path"], e["handler"]) for e in eps] == \
        [("GET", "/vets", "vetsJson")]
