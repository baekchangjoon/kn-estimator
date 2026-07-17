# 인벤토리 재현율 수리 (B1·B2) + JPA 엔티티 1-hop — 설계

작성: 2026-07-17 · 근거: 외부 프로젝트 일반화 시험(`/home/baek/temp/kn-test-results/REPORT.md`,
대상 21회 실행 — spring-petclinic, tainted-spring 11종 등)

## 배경

외부 프로젝트 시험에서 인벤토리 계층의 조용한 누락 2건과 JPA 데이터 계층 과소 반영을
확인했다. petclinic 실측 재현율 10/13 (77%).

- **B1**: `endpoints.py`의 `DECL_RE` 문자 클래스 `[\w<>,\[\]. ]`에 `?`가 없어
  `public ResponseEntity<?> login(...)` 핸들러가 메서드로 인식되지 않는다
  (petclinic AuthController 2 EP 조용히 누락).
- **B2**: `public @ResponseBody Vets foo()`처럼 반환 타입 위치의 인라인
  `@ResponseBody`가 ①`DECL_RE`의 `@` 미허용으로 메서드 인식 실패, ②`is_json` 판정이
  선행 어노테이션 블록만 검사해 이중으로 누락된다 (petclinic VetController 1 EP).
- **JPA**: 데이터 계층 크기 신호가 없다. MyBatis는 DAO에서 SQL XML을 조인해 가산하지만,
  Spring Data 리포지토리는 얇은 인터페이스 파일 크기만 반영되고 엔티티·JPQL은 비가시다
  (tainted-spring 5개 JPA 서비스 실측: 엔티티 파일이 슬라이스에 0건).

## 결정 (사용자 승인 2026-07-17)

1. 세 작업 모두 진행, 한 브랜치(`fix/inventory-recall-jpa-1hop`)에서 커밋 분리.
2. JPA 엔티티는 **리포지토리와 동일 감쇠** — MyBatis XML 조인과 대칭
   (XML이 DAO의 SQL이듯 엔티티는 리포지토리의 데이터 스키마).

## 설계

### B1 — `DECL_RE` 와일드카드 허용

`endpoints.py:25` 문자 클래스에 `?` 추가:

```
public\s+[\w<>,\[\]. ]+\s+(\w+)\s*\(   →   public\s+[\w<>,\[\].? ]+\s+(\w+)\s*\(
```

`?`는 자바에서 제네릭 인자 안에서만 나타나므로 오탐 경로가 없다.

### B2 — 인라인 `@ResponseBody`

- `DECL_RE`가 `public` 뒤의 인라인 어노테이션을 허용:
  `public\s+(?:@\w+\s+)*[\w<>,\[\].? ]+\s+(\w+)\s*\(`.
- `scan()`의 JSON 판정을 선언 라인까지 확장: `_methods`가 산출하는 `body`의 첫 줄이
  선언부이므로 `"@ResponseBody" in anns` → `... or "@ResponseBody" in body.split("\n", 1)[0]`
  (본문 전체를 검사하면 문자열 리터럴 오탐 여지가 있어 선언 라인로 한정).
- **배열형 매핑 값 파싱 포함**: B2가 살리는 실측 사례(petclinic VetController)가
  `@GetMapping({ "/vets" })` 형태다. `_attr`의 value 폴백이 `{ "..." }`도 매칭하도록
  확장하지 않으면 경로가 빈 값이 된다 — 첫 원소만 채택한다 (다중 경로 매핑의 완전
  지원은 범위 밖).
- 참고: `_handler_span_tokens`(scan.py)는 `_methods`를 공유하므로 자동으로 함께 좋아진다.

### JPA 엔티티 1-hop — MyBatis XML 조인과 대칭

`scan.py`의 DAO/Mapper XML 조인과 같은 자리(`build_slices`의
`type_name.endswith(("DAO","Dao","Mapper","Repository"))` 분기)에서:

1. 해석된 파일이 **인터페이스이고** `extends <Base><Entity, ...>` 형태이며 `<Base>`가
   `Repository`로 끝나면 (예: `JpaRepository<Owner, Integer>`, `CrudRepository<...>`,
   커스텀 `*Repository<...>` 베이스 포괄) 첫 제네릭 인자를 엔티티 타입으로 채택.
2. 엔티티를 `_Index.by_class`로 해석해 **리포지토리와 같은 감쇠**로 파일 토큰을 w에
   가산하고 `files`에 추가.
3. 엔티티 미해석(외부 모듈 등) 시 조용히 생략 — MyBatis XML 미발견과 동일 거동.
   `unresolved` 플래그는 건드리지 않는다 (리포지토리 자체는 해석에 성공했으므로).
4. 엔티티는 주입 그래프 frontier에 넣지 않는다 — 1-hop 한정 (엔티티가 참조하는 다른
   엔티티 연쇄는 범위 밖, YAGNI).
5. 중복 방지: 같은 엔드포인트 슬라이스에서 같은 엔티티 파일이 두 리포지토리를 통해
   두 번 가산되지 않도록 files 기준으로 스킵.

추출 정규식(신규, scan.py):

```python
SPRING_DATA_EXTENDS_RE = re.compile(
    r"\binterface\s+\w+[^{]*?\bextends\b[^{]*?\b\w*Repository\s*<\s*(\w+)")
```

### 행위 보존

- MyBatis 프로젝트(SmartPlant 포함): DAO가 `*Repository<T, ID>`를 상속하지 않으므로
  신규 분기에 진입하지 않는다 — **골든 수치 불변**이 게이트다.
- w는 상대 공변량이므로(스케일 불변) JPA 가산의 효과는 EP 간 상대 순위·파티션 경로다.

## 테스트 (TDD — 착수 전 red 고정)

`tests/test_inventory_recall.py` 신규, `test_improvements.py`의 합성 픽스처 패턴을 따른다
(tempfile로 최소 Spring 프로젝트 생성 — SUT 불필요, 항상 실행).

1. **B1 red**: `@RestController` + `public ResponseEntity<?> foo()` 픽스처 → `scan()`이
   그 핸들러를 산출해야 한다. (대조군: `ResponseEntity<Bar>`는 수리 전에도 통과.)
2. **B2 red**: `@Controller` + `@GetMapping` + `public @ResponseBody Vets foo()` 픽스처 →
   json=True 엔드포인트로 산출.
3. **JPA red**: 컨트롤러 → 서비스 → `interface FooRepository extends JpaRepository<Foo, Long>`
   + `Foo.java` 엔티티 픽스처 → 슬라이스 `files`에 `Foo.java` 포함, w에 엔티티 토큰이
   리포지토리 감쇠로 가산.
4. **JPA 부정 케이스**: 엔티티 파일이 없으면 크래시 없이 생략. MyBatis DAO 픽스처는
   기존 거동 그대로 (엔티티 분기 미진입).
5. 기존 스위트 무회귀: MVC 배제·경로 조인 등은 기존 테스트가 커버.

## 검증 게이트 (PR 전 3게이트)

1. 전체 pytest 통과 (신규 포함, `KN_EXTERNAL_SAMPLE=petclinic` 활성 상태 포함).
2. 결정성: petclinic 3회 연속 `kn-plan.json` sha256 동일.
3. 외부 재검증: petclinic **N=10→13** (AuthController 2 + VetController 1),
   tainted JPA 서비스 슬라이스에 엔티티 파일 등장 + 파티션 불변식 재통과,
   MyBatis 경로 무영향(신규 분기 미진입 확인).

## 범위 밖 (이번 브랜치에서 다루지 않음)

멀티모듈 자동 발견(B3), WebFlux 함수형 라우팅(B4), N=0 exit code 규약(O1),
GUIDE 한계 절 문서화(O2 — 후속 docs 커밋 후보).
