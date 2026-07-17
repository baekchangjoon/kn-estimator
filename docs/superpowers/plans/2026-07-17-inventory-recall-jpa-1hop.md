# 인벤토리 재현율 수리 (B1·B2) + JPA 엔티티 1-hop 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 외부 프로젝트 시험에서 확인된 인벤토리 조용한 누락 2건(B1: `ResponseEntity<?>`,
B2: 인라인 `@ResponseBody`+배열형 매핑)을 수리하고, Spring Data 리포지토리의 엔티티를
MyBatis XML 조인과 대칭으로 w에 1-hop 가산한다.

**Architecture:** `endpoints.py`의 정규식 2건 확장(B1·B2)과 `scan.py`의 `_Index`에
`jpa_entity_for` 추가 + `build_slices`의 DAO/Mapper 분기에서 호출(JPA). 신규 테스트는
합성 픽스처만 사용(SUT 불필요, 항상 실행).

**Tech Stack:** Python 표준 라이브러리만 (기존과 동일). pytest로 실행.

**Spec:** `docs/superpowers/specs/2026-07-17-inventory-recall-jpa-1hop-design.md`

## Global Constraints

- 표준 라이브러리 외 의존성 추가 금지 (README: "표준 라이브러리만 사용").
- MyBatis 경로 행위 보존 — DAO가 `*Repository<T, ID>`를 상속하지 않는 한 신규 분기 미진입.
- 커밋 스타일: 소문자 prefix 영어 명령형 (`fix:`, `feat:`, `docs:`), 본문에 근거 요약.
- 실행: 워크트리 `.venv/bin/python -m pytest` (python3.12 venv, `pip install -e '.[test]'` 완료 상태).
- JPA 엔티티 감쇠 = 리포지토리와 동일 (사용자 결정 2026-07-17).

---

### Task 1: B1 — `ResponseEntity<?>` 핸들러 인식

**Files:**
- Create: `tests/test_inventory_recall.py`
- Modify: `src/kn_estimator/endpoints.py:25` (`DECL_RE`)

**Interfaces:**
- Consumes: `endpoints.scan(root) -> list[dict]` (기존 공개 API, 변경 없음)
- Produces: Task 2·3이 같은 테스트 파일의 `_project` 헬퍼를 사용

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_inventory_recall.py` 신규:

```python
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
```

- [ ] **Step 2: red 확인**

Run: `.venv/bin/python -m pytest tests/test_inventory_recall.py -v`
Expected: FAIL — got에 `/api/auth/plain`만 있고 `/api/auth/login` 없음.

- [ ] **Step 3: 최소 수정** — `endpoints.py:25`의 문자 클래스에 `?` 추가:

```python
DECL_RE = re.compile(r'public\s+[\w<>,\[\].? ]+\s+(\w+)\s*\(')
```

- [ ] **Step 4: green 확인**

Run: `.venv/bin/python -m pytest tests/test_inventory_recall.py tests/ -q`
Expected: 신규 1 passed + 기존 11 passed 유지.

- [ ] **Step 5: 커밋**

```bash
git add tests/test_inventory_recall.py src/kn_estimator/endpoints.py
git commit -m "fix(endpoints): scan handlers with wildcard generics like ResponseEntity<?>"
```

---

### Task 2: B2 — 인라인 `@ResponseBody` + 배열형 매핑 값

**Files:**
- Modify: `src/kn_estimator/endpoints.py` (`DECL_RE`, `_attr`, `scan()`의 `is_json`)
- Test: `tests/test_inventory_recall.py` (append)

**Interfaces:**
- Consumes: Task 1의 `_project` 헬퍼, Task 1 이후의 `DECL_RE`
- Produces: 변경된 `endpoints.scan` 거동 (json 판정에 선언 라인 포함)

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_inventory_recall.py`에 append:

```python
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
```

- [ ] **Step 2: red 확인**

Run: `.venv/bin/python -m pytest tests/test_inventory_recall.py -v`
Expected: 신규 테스트 FAIL — eps가 빈 리스트 (`vetsJson` 미인식).

- [ ] **Step 3: 최소 수정** — `endpoints.py` 세 곳:

```python
# ① DECL_RE — public 뒤 인라인 어노테이션 허용
DECL_RE = re.compile(r'public\s+(?:@\w+\s+)*[\w<>,\[\].? ]+\s+(\w+)\s*\(')
```

```python
# ② _attr — 배열형 { "..." } 값 허용 (첫 원소 채택)
def _attr(args, name):
    if not args:
        return None
    m = re.search(name + r'\s*=\s*\{?\s*"([^"]*)"', args)
    if m:
        return m.group(1)
    if name == "value":
        m = re.search(r'^\s*\{?\s*"([^"]*)"', args)
        return m.group(1) if m else None
    return None
```

```python
# ③ scan()의 is_json — 선언 라인(본문 첫 줄)의 인라인 @ResponseBody 인식
            is_json = (rest_class or "JSON_VIEW" in body or "@ResponseBody" in anns
                       or "@ResponseBody" in body.split("\n", 1)[0])
```

- [ ] **Step 4: green 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전체 통과 (신규 2 + 기존 11).

- [ ] **Step 5: 커밋**

```bash
git add tests/test_inventory_recall.py src/kn_estimator/endpoints.py
git commit -m "fix(endpoints): recognize inline @ResponseBody and array-form mapping values"
```

---

### Task 3: JPA 엔티티 1-hop 조인

**Files:**
- Modify: `src/kn_estimator/scan.py` (`SPRING_DATA_EXTENDS_RE` 신규, `_Index.jpa_entity_for` 신규, `build_slices` DAO/Mapper 분기, 모듈 독스트링 1줄)
- Test: `tests/test_inventory_recall.py` (append)

**Interfaces:**
- Consumes: `_Index.by_class: dict[str, Path]`, `tokens_of(path) -> int`, `DEPTH_DECAY`
- Produces: `_Index.jpa_entity_for(java_file: Path) -> Path | None`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_inventory_recall.py`에 append:

```python
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
```

- [ ] **Step 2: red 확인**

Run: `.venv/bin/python -m pytest tests/test_inventory_recall.py -v`
Expected: JPA 테스트 3건 FAIL (`Owner.java` 미포함 / w 불일치), B1·B2 테스트는 PASS 유지.
(참고: `test_jpa_entity_missing_is_silently_skipped`는 수리 전에도 우연히 통과할 수 있다 —
red의 본체는 나머지 2건이다.)

- [ ] **Step 3: 구현** — `scan.py` 네 곳:

```python
# ① 모듈 상수 근처(PACKAGE_RE 아래)에 추가
# Spring Data 리포지토리: extends <Base>Repository<Entity, ...>의 첫 제네릭 인자가 엔티티.
SPRING_DATA_EXTENDS_RE = re.compile(
    r"\binterface\s+\w+[^{]*?\bextends\b[^{]*?\b\w*Repository\s*<\s*(\w+)")
```

```python
# ② _Index 메서드 추가 (mybatis_xml_for 아래)
    def jpa_entity_for(self, java_file):
        """Spring Data 리포지토리의 엔티티 파일 — MyBatis XML 조인의 JPA 대응.

        `extends *Repository<Entity, ...>`의 첫 제네릭 인자를 해석한다. 미해석(엔티티가
        외부 모듈 등)은 XML 미발견과 동일하게 조용히 None — 리포지토리 자체는 해석에
        성공했으므로 unresolved가 아니다.
        """
        m = SPRING_DATA_EXTENDS_RE.search(java_file.read_text(errors="replace"))
        return self.by_class.get(m.group(1)) if m else None
```

```python
# ③ build_slices의 DAO/Mapper 분기 확장 (MyBatis XML 조인 직후)
                if type_name.endswith(("DAO", "Dao", "Mapper", "Repository")):
                    for x in idx.mybatis_xml_for(f):
                        files.append(str(x.relative_to(root)))
                        w_add += int(tokens_of(x) * decay)
                    ent = idx.jpa_entity_for(f)
                    if ent is not None and str(ent.relative_to(root)) not in files:
                        files.append(str(ent.relative_to(root)))
                        w_add += int(tokens_of(ent) * decay)
```

```python
# ④ 모듈 독스트링에 1줄 추가 (MyBatis 줄 아래) — 문서 드리프트 방지
- JPA: Spring Data 리포지토리의 extends 제네릭 첫 인자(엔티티)를 1-hop 조인.
```

- [ ] **Step 4: green 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전체 통과 (신규 5 + 기존 11).

- [ ] **Step 5: 커밋**

```bash
git add tests/test_inventory_recall.py src/kn_estimator/scan.py
git commit -m "feat(scan): join JPA entity one hop from Spring Data repositories"
```

---

### Task 4: 3게이트 검증 + 외부 재검증

**Files:** 없음 (검증만). 결과는 브랜치 보고에 기록.

**Interfaces:**
- Consumes: 설치된 `kn-estimate` CLI, 시험 대상 (`/home/baek/github_spring-petclinic/spring-petclinic`, `/home/baek/temp/tainted-spring/*`)

- [ ] **Step 1: 게이트① 전체 테스트** (외부 스모크 활성 포함)

Run: `KN_EXTERNAL_SAMPLE=/home/baek/github_spring-petclinic/spring-petclinic .venv/bin/python -m pytest tests/ -q`
Expected: 전부 통과, 실패 0.

- [ ] **Step 2: 게이트② 결정성** — petclinic 3회 실행, `kn-plan.json` sha256 3회 동일.

- [ ] **Step 3: 게이트③ 외부 재검증**

- petclinic: N=10 → **13** (`/api/auth/login`, `/api/auth/validate`, `GET /vets` 추가).
- tainted-spring JPA 서비스(auth-user, diary 등): 슬라이스 `files`에 엔티티 등장, w 증가.
- 파티션 불변식 재검사 (전 EP 커버, 중복 0, peak ≤ w_hard).
- MyBatis 무영향: 신규 분기가 interface+`*Repository<` 요구 — tainted·petclinic 외
  변화가 예상 경로뿐임을 diff로 확인.

Expected: 위 수치 재현. 벗어나면 커밋 전 원인 규명.
