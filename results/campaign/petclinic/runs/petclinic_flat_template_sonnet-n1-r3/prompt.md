# NIMBUS 실행 지시 — flat_template / run_id=petclinic_flat_template_sonnet-n1-r3 / N=1

대상 프로젝트: 현재 디렉토리 (Java/Spring — 빌드 체계·스택은 Step 1에서 스스로 탐지하라).

대상 엔드포인트 (1개):
- GET /api/owners/{ownerId}  (controller: src/main/java/org/springframework/samples/petclinic/owner/OwnerRestController.java, handler: getOwner)

산출물 요구: 엔드포인트별 RestAssured 블랙박스 통합 테스트 + 전처리 SQL + (외부 연동 시)
WireMock 스텁. 경로는 아래 지침의 표준 경로를 따른다.

## 토폴로지 규칙 (flat + template)

- **서브에이전트 위임 툴(Agent/Task)을 사용하지 마라.** 네가 직접 전 단계를 수행한다.
- 아래 "전체 파이프라인 지침"의 **모든 단계(Pre-Step ~ Step 5)를 지침 그대로 수행**하라
  — 환경 분석, 스택 탐지, 스킬 실행, 테스트 플랜, VTC 플랜, 인증/외부/DB 분석 전부.
  Step 6(실행/커버리지)만 제외.
- **단, 산출물 "저작"만 템플릿 렌더러로 치환한다** (아래 치환 규칙).

## 템플릿 치환 규칙 (지침의 저작 부분을 이것으로 대체)

62KB 지침 중 SQL문·WireMock JSON·테스트 자바 코드를 **직접 타이핑하는 부분만**
아래로 대체한다. 무엇을 만들지 결정하는 분석·플랜은 전부 지침대로 수행한 뒤:

1. **(프로젝트당 1회, Pre-Step에서)** 환경 분석 산출물에서 접속 정보를 추출해
   `.nimbus/artifacts/support-config.json` 작성 (스키마는 아래 문서 참조).
2. **스키마 학습 (1회)**: `nimbus-skills/template-render/spec-schema.md`를 Read.
3. **엔드포인트마다**: Step 2/2-B의 테스트 플랜과 Step 3의 분석 산출물(인증 플로우,
   외부 호출, DB 데이터 요구)을 근거로 `.nimbus/specs/<slug>.json` 작성 —
   - 인증: 카탈로그 패턴 + 파라미터로 (custom은 카탈로그 표현 불가 시에만)
   - DB 픽스처: 구조화(table/values/where)로 — SQL 문자열 직접 작성 금지
     (rawSetupSql 폴백은 구조화 표현 불가 시에만)
   - 스텁: request/response 구조화로
   - tests: Step 2 플랜의 NTC 케이스 + Step 2-B 플랜의 VTC 케이스를 반영
4. **렌더**: `python3 nimbus-skills/template-render/main.py --spec .nimbus/specs/<slug>.json`
   렌더러가 테스트 클래스·SQL·스텁 생성 + manifest 자동 병합 (manifest 직접 작성 금지).
5. Step 5(정적 검증)는 지침대로 수행하되, 결함 수정도 자바 파일 직접 수정이 아니라
   **spec 수정 → 재렌더**로 하라.

## 단계 구분자 (계측 — 반드시 준수)

- **각 단계 작업을 시작하기 직전**, Bash로 정확히 아래 한 줄을 실행하라:

```
echo "NIMBUS_STAGE_BEGIN <단계> <대상>"
```

- `<단계>` ∈ `prestep | step1 | step0 | step2 | step2b | step3 | step4 | step5`
  (지침의 Pre-Step~Step 5와 동일한 의미다. spec 작성+렌더 실행은 step4에 해당한다.)
- `<대상>`: 프로젝트 전역 단계(prestep, step1)는 `GLOBAL`,
  엔드포인트 단위 단계는 `"<METHOD> <path>"` (엔드포인트 표의 표기 그대로).
- 같은 단계라도 **대상 엔드포인트가 바뀌면 다시 실행**하라.
- **라벨 의미 명확화**: 최초 저작(spec 작성+렌더)은 `step4` 마커 아래에서,
  검증 및 검증 실패 후 수정(spec 수정→재렌더)은 `step5` 마커 아래에서 수행하라.
- 이 echo 외의 용도로 `NIMBUS_STAGE_BEGIN` 문자열을 출력하지 마라.

## 계측 규칙 (모든 암 공통 — 반드시 준수)

- 시작 시 `diagnostics/` 디렉토리를 만들라 (`mkdir -p diagnostics`).
- 서브에이전트에 위임하기 **직전**마다 `date -u +%s`로 시각을 얻고, 아래 형식의 JSON 1줄을
  `diagnostics/run_ledger_layerA.jsonl`에 append 하라:

```
{"run_id":"petclinic_flat_template_sonnet-n1-r3","role":"subagent","agent_name":"<에이전트명>","stage":"<스테이지(step0~5/prestep)>","model_requested":"<해당 에이전트의 frontmatter 모델>","endpoints":["<METHOD> <path>", ...],"endpoint_count":<수>,"ts_start_epoch":<epoch>,"context_shared_chars":<위임 프롬프트 중 공유분 문자수>,"context_variable_chars":<가변분 문자수>,"notes":""}
```

- 서브에이전트가 복귀하면 `date -u +%s`로 시각을 얻어 아래 1줄을 append 하라
  (같은 agent_name, role만 다름):

```
{"run_id":"petclinic_flat_template_sonnet-n1-r3","role":"subagent_end","agent_name":"<에이전트명>","ts_end_epoch":<epoch>,"output_chars":<반환 텍스트 문자수>,"notes":""}
```

- 파이프라인 종료 직전, 너 자신의 활동을 1줄로 기록하라:

```
{"run_id":"petclinic_flat_template_sonnet-n1-r3","role":"orchestrator","delegations":<위임 횟수>,"notes":"<몇 번 위임하고 몇 번 결과를 읽었는지>"}
```

- 토큰/크레딧/비용 수치는 네가 볼 수 없다. **절대 기입하거나 추정하지 마라**
  (사후에 인프라 로그로 조인된다).

## 산출물 매니페스트 (모든 암 공통 — 품질 게이트가 이 파일을 검사한다)

- 파이프라인 종료 직전, `.nimbus/artifacts/manifest.json`을 작성하라. 형식:

```
{"<METHOD> <path>": {"status": "ok" | "fail", "files": ["<생성한 파일 경로1>", ...]}, ...}
```

- **요청받은 모든 엔드포인트가 키로 존재해야 한다.** 각 엔드포인트의 `files`에는 그
  엔드포인트를 위해 생성한 테스트 코드/SQL/스텁 파일의 실제 경로를 전부 나열하라.
  생성에 실패한 엔드포인트는 status를 "fail"로 정직하게 기록하라.

## 실행 범위 제한 (모든 암 공통)

- Step 6(테스트 실행/자가수정/JaCoCo 커버리지)은 수행하지 않는다.
- docker compose 기동·검증, DB 접속, 서버 기동은 수행하지 않는다. 산출물(파일) 작성까지만.
- `mvn test` / `mvn verify` 실행 금지. (컴파일 검증은 하네스가 사후 수행한다.)


> **계측 규칙 중 매니페스트 항목 오버라이드**: `.nimbus/artifacts/manifest.json`은
> 렌더러가 엔드포인트마다 자동 병합하므로 **직접 작성하지 마라**. 파이프라인 종료 전
> 매니페스트에 요청 엔드포인트 1개가 전부 있는지 확인만 하라.

## 아래 지침의 저작 관련 절 오버라이드 (template 모드에서 우선)

원본 지침(Step 3~5)은 다음을 요구하지만, template 모드에서는 **spec 계약으로 대체**한다.
아래는 원본 지침을 읽을 때 적용할 매핑이다 — 원본 지침과 충돌하면 이 절이 우선한다.

| 원본 지침이 요구하는 것 | template 모드에서의 처리 |
|---|---|
| 테스트 자바 코드를 직접 작성 (RestAssured 체인, matcher) | spec.tests[]로 기술 → 렌더러 생성. `equalTo/containsString/notNullValue/equalToInt` assertion 지원 |
| 테스트 본문에서 JDBC로 DB 상태 검증 | spec.tests[].dbAssertions[]로 기술 (아래) → 렌더러가 queryScalar 검증 생성 |
| SQL setup/teardown 문 직접 작성 | spec.fixtures 구조화 → 렌더러 합성 (raw 폴백은 최소화, 계측됨) |
| WireMock stub JSON 직접 작성 / 런타임 stubFor 등록 | spec.stubs[] 구조화(정적 매핑 파일) → 렌더러 생성. 런타임 등록·OTEL baggage 격리가 꼭 필요하면 그 엔드포인트는 이유를 원장 notes에 남기고 raw 경로로 (계측) |
| NTC≥3, VTC/ATC 전량 반영 | 동일하게 지킨다 — Step 2/2-B 플랜의 케이스 수를 spec.tests[]에 그대로 반영(NTC_/VTC_/ATC_ 접두). 케이스 수는 줄이지 마라 |

> 즉 "무엇을 검증할지"(케이스 수·DB 검증·분기 커버리지)는 원본 지침대로 유지하고,
> "어떻게 타이핑할지"만 spec으로 옮긴다. 표현 불가한 고급 요구는 raw/custom 폴백으로
> 처리하되 사용을 최소화하라(`template-usage.json`에 집계된다).

## 전체 파이프라인 지침 (원본 통짜 지침 — 저작 부분만 위 치환 규칙 적용)

---
inclusion: always
---

# NIMBUS — API Integration Test Automation

You are an expert API Integration Test engineer. Generate hallucination-free Black-Box API Integration Tests by orchestrating the provided static analysis skills in a strict multi-step pipeline (Step 0 → 1 → 2 → 3 → 4 → 5 → 6).

> **스킬 설계 원칙**: 각 스킬은 LLM이 소스코드를 직접 읽는 것 대비 토큰을 절약하기 위해 존재한다.
> 스킬 산출물은 수천 줄의 소스를 수백 줄로 압축하여 컨텍스트 비용을 ~80% 절감한다.

## 🎯 Core Principles

1. **내부 클래스 import 금지**: 프로젝트의 Controller, Service, VO, Mapper 등 어떤 내부 클래스도 import하지 않는다.
2. **순수 HTTP 테스트**: RestAssured로 HTTP 요청/응답만으로 검증한다.
3. **내부 모킹 금지**: `@MockBean`, `@Autowired`, `@SpringBootTest`, `mockConstruction`, `mockStatic` 등 내부 구현에 의존하는 어노테이션/모킹을 사용하지 않는다.
4. **외부 연동은 WireMock만 (HTTP 기반 한정)**: WireMock은 HTTP/HTTPS 기반 외부 연동에만 적용한다. skill 출력에서 `wiremock_applicable: false`이거나 `mockStatic()`, `MockedConstruction<T>` 등을 추천하더라도 테스트 코드에서 내부 모킹을 사용하지 않는다. HTTP 기반이 아닌 외부 연동은 테스트 서버의 프로파일/프로퍼티 설정으로 사전 처리한다.
5. **DB 검증은 직접 JDBC**: `@Autowired Repository` 대신 별도 JDBC 연결로 DB 상태를 검증한다.
6. **Real Database**: 프로젝트의 REAL database를 사용한다 (NOT H2/in-memory).
7. **Zero Hallucination**: NEVER guess DTO fields, bean names, URLs, or types. Use ONLY values from skill outputs.
8. **RestAssured Mandatory**: All HTTP calls in tests MUST use `rest-assured`.
9. **이식성**: 테스트 코드를 별도 프로젝트로 옮겨도 그대로 동작해야 한다.
10. **구체적 Assertion 필수 (No Weak Matchers)**: `notNullValue()`, `not(empty())`, `greaterThanOrEqualTo(1)` 같은 느슨한 matcher만으로 응답 필드를 검증하지 않는다. 어떤 값이 와도 통과하는 assertion은 테스트가 아니다. 반드시 setup SQL에 삽입한 테스트 데이터의 실제 값과 `equalTo()`로 1:1 비교한다. 날짜처럼 동적으로 계산되는 값은 `matchesPattern()`으로 포맷을 검증한다.

---

## 📚 Reference Documents (`.nimbus/references/`)

`.nimbus/references/` 폴더에 DDL 파일과 API 스펙 문서가 존재할 수 있다. 파이프라인 시작 전에 이 폴더를 확인하고, 존재하는 문서를 테스트 생성에 적극 참조한다.

### 탐색 규칙

| 문서 유형 | 파일 패턴 | 예시 |
|-----------|-----------|------|
| DDL (스키마 정의) | `.nimbus/references/*.sql` | `schema.sql`, `init.sql` |
| API 스펙 문서 | `.nimbus/references/*api*.yml` 또는 `.nimbus/references/*api*.yaml` | `open_api.yaml`, `api_spec.yml` |

### 활용 방법

* **DDL 파일** (`.sql`):
  - **⚠️ DDL 파일은 반드시 전체를 끝까지 읽어야 한다 (CRITICAL).** 파일이 truncation되면 나머지를 이어서 읽는다. 트리거, 시퀀스, 프로시저 등 DB 오브젝트 정의가 파일 후반부에 위치하는 경우가 많으며, 이를 놓치면 setup SQL에서 PK 중복, FK 위반 등 런타임 에러가 발생한다.
  - 확인 대상: 테이블 구조(컬럼, NOT NULL, FK, UNIQUE) + **트리거(TRIGGER)** + 시퀀스(SEQUENCE) + 프로시저(PROCEDURE)
  - **트리거 확인 필수**: `INSERT TRIGGER`가 있는 테이블은 setup SQL에서 트리거가 자동 INSERT하는 대상 테이블을 직접 INSERT하면 안 된다. 트리거 소스 테이블의 INSERT만으로 데이터를 생성하고, 트리거가 자동 생성하는 테이블은 setup SQL에서 제외한다.
  - Step 3-B(`analyze-auth-flow`)의 `--ddl-path` 인자로 사용할 수 있다 (기존 `db/00-init-schema.sql` 대신 또는 보완).
  - Step 3-D(Database State)에서 테이블 구조, NOT NULL 컬럼, FK 관계를 파악할 때 참조한다.
  - setup/teardown SQL 생성 시 정확한 컬럼명과 제약조건을 이 DDL에서 확인한다.

* **API 스펙 문서** (`.yml`/`.yaml`):
  - Step 3-A에서 Controller 소스 직접 분석 결과와 교차 검증하여 요청/응답 스펙의 정확도를 높인다.
  - 엔드포인트 경로, HTTP 메서드, 요청 파라미터, 응답 필드명/타입을 확인한다.
  - Controller 소스코드와 API 스펙 문서가 충돌하면 **소스코드를 우선**한다 (테스트는 현재 동작하는 코드를 검증하는 것이 목적이므로).
  - 불일치가 발견되면 테스트 코드 상단 주석 또는 별도 리포트로 **불일치 목록을 명시**한다 (어느 쪽이 맞는지는 사람이 판단).

### 파이프라인 적용

* **Step 0 시작 전**: `.nimbus/references/` 폴더를 스캔하여 `*.sql` 파일과 `*api*.yml`/`*api*.yaml` 파일 존재 여부를 확인한다.
* 파일이 존재하면 해당 내용을 읽어 이후 파이프라인 전반에서 참조 자료로 활용한다.
* 파일이 없으면 기존 파이프라인을 그대로 수행한다 (선택적 참조).

---

## 🚀 Skill Pipeline (Step 0 ~ 6)

### Pre-Step: 테스트 환경 구성 (1회)

> **프로젝트당 1회만 실행**. 산출물(`.nimbus/artifacts/test-env/{name}.json`)이 이미 존재하면 스킵한다.

* **Execute**: `setup-test-env` — 프로젝트 분석 및 테스트 환경 정보 산출물 생성
  ```bash
  python3 nimbus-skills/setup-test-env/main.py --path .
  ```

* **산출물 확인**: `.nimbus/artifacts/test-env/{ProjectName}.json`
  * 서버 기동 방식, DB 종류/접속 정보, WireMock 포트, context path, JaCoCo 설정, DDL 위치

* **Action**: 산출물을 참조하여 다음 파일을 생성한다 (없는 경우에만):
  * `docker/Dockerfile` — 서비스 빌드 (WAR/JAR → 이미지). OTEL Java Agent 사용 시 `COPY target/otel-agent/opentelemetry-javaagent.jar /app/opentelemetry-javaagent.jar` 포함.
  * `docker/docker-compose.yml` — 전체 테스트 환경 (app + DB + WireMock + LocalStack, JaCoCo agent 포함). app 서비스에 OTEL Java Agent(`-javaagent`) + `OTEL_*` env 포함 (아래 체크리스트 7번).
  * `docker/docker-compose.coverage.yml` — JaCoCo 커버리지 측정용 override. app 서비스 `JAVA_TOOL_OPTIONS`에 OTEL + JaCoCo 이중 `-javaagent`를 주입한다 (아래 체크리스트 7번).
  * `docker/init-db/` — DDL 초기화 (`.nimbus/references/schema.sql` 참조). DDL 작성 시 mapper XML의 모든 테이블/컬럼을 빠짐없이 포함해야 한다. 이력 테이블(`*_HIST`), 관리자 테이블 등 teardown SQL에서 참조하는 테이블도 반드시 포함.
  * `docker/jacoco-agent/jacocoagent.jar` — Maven Central에서 다운로드 또는 Maven 로컬 저장소에서 복사
  * `target/otel-agent/opentelemetry-javaagent.jar` — OpenTelemetry Java Agent(v2.12.0)를 [OTEL releases](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases)에서 다운로드. CI에서는 `api-integration-test.yml`의 `Prepare OTEL Agent` step이 수행한다. Dockerfile이 이 경로를 `COPY`하므로 이미지 빌드 전에 존재해야 한다.
  * `docker/conf-override/` — 외부 라이브러리(XPay 등)가 참조하는 설정 파일의 Docker용 오버라이드. `src/test/resources/wiremock/` 하위에 WireMock용 설정 파일이 이미 존재하면 이를 기반으로 Docker용(`localhost` → Docker 서비스명)으로 수정하여 생성.

* **Docker 환경 구성 시 필수 체크리스트**:

  **1. 프로퍼티 Validation 필드**: `@Validated` + `@NotBlank` 등이 설정된 프로퍼티 클래스의 필드가 빈 값이면 서버 기동이 실패한다. 테스트용 더미 값을 설정해야 한다. (`src/main/resources` 하위 설정 파일 수정 허용)

  **2. 외부 라이브러리 설정 파일 마운트**: 서버 코드 또는 외부 JAR이 특정 경로의 설정 파일을 참조하는 경우, 해당 경로에 Docker 볼륨 마운트로 설정 파일을 제공해야 한다. `src/test/resources/wiremock/` 하위에 WireMock용 설정 파일이 이미 존재하면 이를 기반으로 Docker용(`localhost` → Docker 서비스명)으로 수정하여 `docker/conf-override/`에 생성하고 마운트한다.

  **3. HTTPS 외부 연동 → WireMock HTTPS**: 외부 라이브러리가 HTTPS만 지원하는 경우, WireMock에 `--https-port` 활성화 + 설정 파일의 URL을 `https://<wiremock서비스명>:<https포트>/...`으로 변경 + TLS hostname/cert 검증 비활성화 설정 확인. 실제 외부 도메인을 WireMock으로 resolve해야 하면 Docker 네트워크 alias를 사용한다.

  **4. 외부 라이브러리 런타임 의존성**: 로컬 JAR(`lib/*.jar`)이 런타임에 추가 라이브러리를 필요로 하는 경우 `build.gradle`/`pom.xml`에 의존성을 추가한다.

  **5. 외부 라이브러리 로그 디렉토리**: 설정 파일의 로그 경로가 컨테이너 내 존재하는 경로를 가리켜야 한다. 존재하지 않으면 `FileNotFoundException`으로 실패할 수 있다.

  **6. DDL 완전성**: DDL 작성 시 mapper XML의 모든 테이블/컬럼을 빠짐없이 포함해야 한다. 이력 테이블(`*_HIST`), 관리자 테이블 등 teardown SQL에서 참조하는 테이블도 반드시 포함.

  **7. OTEL Java Agent (병렬 WireMock stub 격리)**: 병렬 테스트에서 WireMock stub을 메서드별로 격리하려면(Step 3-C "OTEL Baggage 기반 WireMock stub 격리"의 **전제 조건**) app 서버에 OTEL Java Agent를 부착해야 한다. `setup-test-env` 산출물의 `otel` 섹션을 참조하여 다음을 생성한다.

  * **Dockerfile** — agent jar를 이미지에 복사:
    ```dockerfile
    COPY target/otel-agent/opentelemetry-javaagent.jar /app/opentelemetry-javaagent.jar
    ```
  * **docker-compose.yml** (app 서비스 `environment`) — `JAVA_TOOL_OPTIONS`에 `-javaagent`를 추가하고 OTEL env를 설정 (exporter는 테스트 전용이므로 모두 `none`, baggage propagator 활성화):
    ```yaml
    JAVA_TOOL_OPTIONS: -DLOCALSTACK_ENDPOINT=http://localstack:4566 -javaagent:/app/opentelemetry-javaagent.jar
    OTEL_TRACES_EXPORTER: none
    OTEL_METRICS_EXPORTER: none
    OTEL_LOGS_EXPORTER: none
    OTEL_PROPAGATORS: tracecontext,baggage
    ```
  * **docker-compose.coverage.yml** (app 서비스) — OTEL agent를 JaCoCo agent보다 **먼저** 부착하여 이중 `-javaagent`로 주입:
    ```yaml
    JAVA_TOOL_OPTIONS: >-
      -javaagent:/app/opentelemetry-javaagent.jar
      -javaagent:/app/jacocoagent.jar=output=tcpserver,address=*,port=6300,append=false
    OTEL_TRACES_EXPORTER: none
    OTEL_METRICS_EXPORTER: none
    OTEL_LOGS_EXPORTER: none
    OTEL_PROPAGATORS: tracecontext,baggage
    ```
  * **agent jar provisioning**: `target/otel-agent/opentelemetry-javaagent.jar`가 빌드 전에 존재해야 한다. CI(`api-integration-test.yml`)는 JaCoCo agent 다운로드 step과 나란히 `Prepare OTEL Agent` step에서 OTEL release(v2.12.0)를 내려받는다.

* **검증**: `docker compose -f docker/docker-compose.yml up -d`로 전체 환경(app + DB + WireMock)이 정상 기동되는지 확인. app 서비스의 health check가 healthy가 되면 성공. 이 기동 방식은 `test-execution.md` Phase 1에서 `jacoco-server` 스킬이 `--docker-compose` 모드로 app을 기동/종료하는 방식과 동일하다.

### Step 0: Dependency Tree 산출물 확인 (Fast Path)

> **파이프라인 단위**: API 엔드포인트(Controller 내 단일 핸들러 메서드) 단위로 실행한다.
> 하나의 Controller에 엔드포인트가 N개이면, 각 엔드포인트마다 Step 0~6을 개별 실행한다.

* **확인**: `.nimbus/artifacts/dependency-tree/{ControllerName}_{method}.md` 파일이 존재하는지 확인한다.
  * fallback: `{ControllerName}_{method}.md`가 없으면 `{ControllerName}.md`도 확인한다.
* **존재하면 (Fast Path)**:
  * 산출물 파일을 읽어 의존성 트리, DB 테이블 요약, 외부 연동 요약을 참조한다.
  * Step 1(`detect-dev-stack`)은 산출물이 없으면 실행하되, Step 3의 하위 파일 탐색(Service, Mapper, MyBatis XML, VO 등)을 스킵한다.
  * 산출물의 정보만으로 테스트 시나리오 설계, SQL 생성, WireMock 판단을 수행한다.
  * 단, `generate-test-plan` 스킬(Step 2)은 Controller/Service 메서드의 분기 로직 분석이 필요하므로 여전히 실행한다.
* **존재하지 않으면**: 기존 Step 1~3를 순서대로 실행한다.
* **산출물 생성**: `dependency-tree` 스킬을 실행하여 산출물을 미리 생성할 수 있다.
  ```bash
  python3 skills/dependency-tree/main.py --file <CONTROLLER_FILE> --method <HANDLER_METHOD>
  ```

### Step 1: Project Environment Discovery (Foundation)

`detect-dev-stack` 스킬을 실행하여 프로젝트 기술 스택을 파악한다.
이 스킬은 pom.xml(~20KB)을 읽는 대신 구조화된 JSON(~2KB)으로 압축하여 토큰을 절약한다.
**프로젝트당 1회만 실행**하면 되며, 산출물을 캐싱하여 이후 세션에서 재사용한다.

* **Execute**:
  `skills/detect-dev-stack` — 프로젝트 기술 스택 탐지
  ```bash
  python3 nimbus-skills/detect-dev-stack/main.py --path .
  ```

* **Action**:
  * `detect-dev-stack` 출력에서:
    * `testing.framework` → Use Java (junit-jupiter) or Groovy (spock)
    * `database` & `orm` → Identify the actual DB (MariaDB, MySQL, PostgreSQL)
    * `test_env_compat` → JUnit, RestAssured, WireMock 호환 버전 확인
    * 테스트 대상 서버는 외부에서 기동된 실제 서버를 사용한다 (`@SpringBootTest` 사용 안 함)
  * **테스트 대상 컨트롤러 엔드포인트**: 사용자가 지정한 Controller 파일을 직접 읽어 `@PostMapping`, `@GetMapping` 등의 어노테이션에서 HTTP method와 URL path를 확인한다.

### Step 2: Test Plan Generation (Test Design Foundation)

* **Execute**: `skills/generate-test-plan` — 내부에서 `extract-key-params` + `extract-test-scenarios`를 자동 호출하여 파라미터 영향도 분석과 분기 시나리오 추출을 통합 수행
  ```bash
  python3 skills/generate-test-plan/main.py --file <SERVICE_FILE> --method <METHOD_NAME>
  ```
  * 산출물: `.nimbus/artifacts/test-plan/{ClassName}_{MethodName}.md`
  * 이 산출물이 이후 Step 4, Step 6의 기준 체크리스트가 된다.
  * 동일 API에 대해 반복 요청해도 항상 동일한 테스트 케이스 목록이 보장된다.
  * 내부 교차 검증: NullUtil.isNone(X) 패턴은 VTC_missing_X와 자동 매핑되어 중복 케이스 제거, API 파라미터로 제어 불가능한 내부 데이터 의존 분기는 자동 제외
  * **NOTE**: `extract-key-params`와 `extract-test-scenarios`는 이 스킬이 내부에서 자동 호출하므로 별도 실행하지 않는다.

* **Output**: 각 요청 파라미터의 비즈니스 로직 영향도 분류
  * 🔴 RED (직접 영향): 분기 조건, DB WHERE, 에러 트리거, equals 비교, stream filter에 사용
  * 🟡 YELLOW (간접 영향): 값이 덮어쓰기되거나 하위 메서드에 전달만 됨
  * ⚪ WHITE (저장만): Mapper 변환 → DB 저장, 분기 없음

* **Action — 파라미터 분석 결과를 테스트 설계에 반영**:

  | 영향도 | 테스트 설계 적용 |
  |--------|-----------------|
  | 🔴 RED | 반드시 VTC/ATC 테스트 케이스 생성. VTC 표준 rule에 따라 조건부 적용. `test_suggestion` 필드의 에러코드와 조건을 테스트 시나리오에 직접 반영 |
  | 🟡 YELLOW | 선택적 테스트. 덮어쓰기 로직이 있으면 덮어쓰기 전/후 값 차이 검증 고려 |
  | ⚪ WHITE | NTC에서 정상 저장 확인만. 별도 VTC/ATC 불필요 |

* **VTC 표준 Rule (RED 파라미터 대상)**:

  `generate-vtc-from-red-params` 스킬이 RED 파라미터에 아래 7가지 rule을 조건부로 적용하여 VTC 케이스를 자동 생성한다.
  타입 정보(`java_type`)나 DDL 컬럼 길이(`ddl_max_length`)를 추출할 수 없는 경우, 해당 rule은 적용하지 않고 무시한다.

  | Rule | 적용 조건 | 이유 | 테스트 값 예시 |
  |------|-----------|------|---------------|
  | NULL | RED 전체 무조건 | 거의 항상 분기 존재 | 파라미터 생략 또는 `null` |
  | EMPTY | RED 전체 무조건 | isEmpty 체크 보편적 | `""` |
  | WHITE_SPACE | RED 중 DB_WHERE, EQUALITY_CHECK | trim 누락 버그 탐지 | `" \t\n"` |
  | SPECIAL_CHAR | RED 중 DB_WHERE | SQL injection 방어 검증 | `"'; DROP TABLE--"` |
  | LENGTH_EXCEEDED | RED 중 boundary 힌트 또는 DDL 컬럼 길이 확인 가능 | 길이 제약 없으면 무의미 | DDL max+10 길이 문자열 |
  | LENGTH_SHRUNK | 위와 동일 | 동일 | 길이 1 또는 경계값 미만 |
  | INVALID_FORMAT | RED 중 날짜/숫자 파싱 로직 감지 | String 파라미터엔 무의미 | `"not-a-date"`, `"abc"` |

  **VTC 테스트 메서드 네이밍**: `VTC_{paramName}_{RULE}` (예: `VTC_entrId_NULL`, `VTC_svcCd_EMPTY`, `VTC_mrktId_SPECIAL_CHAR`)

* **`direct_input` vs `via_internal_transform` 활용**:
  * `direct_input` 파라미터: API 요청에서 직접 제어 가능 → VTC/ATC 테스트 데이터로 직접 사용
  * `via_internal_transform` 파라미터: 내부 mapper 변환 후 사용 → setup SQL에서 해당 필드값을 맞춰야 함 (API 요청으로 직접 제어 불가)

* **NTC 테스트 데이터 설계 원칙**:
  * RED 파라미터의 `reasons` 필드에서 BRANCH_CONDITION, EQUALITY_CHECK 조건을 확인
  * 해당 조건을 통과하는 값으로 setup SQL과 요청 파라미터를 설계
  * 예: `encnDTO.getMrktId().equals(entrInfo.getMrktId())` → setup SQL의 mrktId와 요청의 mrktId를 동일하게 설정

### Step 2-B: VTC Mutation Plan (RED 파라미터 변조 테스트 자동 생성)

> Step 2의 `extract-key-params` 결과(RED 파라미터 + 메타데이터)를 입력으로 사용한다.
> Step 2 완료 후 바로 실행하며, Step 3과는 독립적이다.

* **Execute**: `skills/generate-vtc-from-red-params` — RED 파라미터에 VTC 표준 rule 적용 + Java 코드 스니펫 생성
  ```bash
  python3 skills/generate-vtc-from-red-params/main.py \
    --params-json .nimbus/artifacts/key-params/{ClassName}_{MethodName}.json \
    --ddl-path docker/init-db/00-init-schema.sql \
    --api-path <API_ENDPOINT_PATH> \
    --http-method POST \
    --content-type URLENC
  ```
  * `--params-json` 대신 `--file` + `--method`로 직접 추출도 가능
  * `--ddl-path`는 선택 사항 (extract-key-params가 `ddl_max_length`를 이미 추출한 경우 보완용)
  * `--api-path` 지정 시 API 레벨 변조 패턴 6건(emptyBody, overflow, XSS, badHeader, structureTamper, wrongMethod)도 추가 생성

* **산출물**: `.nimbus/artifacts/vtc-plan/{ClassName}_{MethodName}.md`
  * 파라미터별 VTC 변조 목록 (직접 제어 7 rules + 간접 제어 DB/외부연동 rules)
  * API 레벨 변조 패턴 6건
  * 각 VTC 케이스의 Java 코드 스니펫 (RestAssured 기반)

* **Action**:
  * Step 4(Test Code Generation)에서 VTC 테스트 메서드 작성 시 이 산출물의 코드 스니펫을 기반으로 생성
  * Step 2의 test-plan 산출물과 함께 체크리스트 대조 대상이 됨

### Step 3: Parallel Analysis (Auth + External Dependencies + DB Data)

Step 1~2-B의 결과를 기반으로 3개 서브그룹을 **병렬 실행**한다. 각 서브그룹은 서로 의존성이 없다.

#### Step 3-A: API Spec — Controller 직접 분석 (스킬 없음)

> `extract-api-specs` 스킬은 `request.getParameter()` 패턴을 감지하지 못하는 등 정확도가 낮아 제거되었다.
> 대신 LLM이 Controller 소스를 직접 읽어 API 스펙을 파악한다. Controller는 이미 Step 2에서 읽었으므로 추가 토큰 비용이 없다.

* **Action**: Controller 메서드에서 다음을 직접 파악한다:
  * HTTP method, path (`@PostMapping`, `@GetMapping` 등)
  * Request parameters (`request.getParameter()`, `@RequestParam`, `@PathVariable`, `@RequestBody` DTO)
  * Response 필드 (`result.put()`, `response.setXxx()` 등)
  * Validation annotations (`@NotNull`, `@Size` 등)이 있으면 VTC 테스트 케이스에 반영
* `.nimbus/references/` 폴더에 API 스펙 문서(`*api*.yml`/`*api*.yaml`)가 있으면 교차 검증에 활용한다.

#### Step 3-B: Auth Flow Analysis (인증/인가 흐름 분석)

* **Execute**: `skills/analyze-auth-flow` — 인터셉터/필터 체인의 인증 흐름을 정적 분석
  ```bash
  python3 skills/analyze-auth-flow/main.py \
    --src-root src/main/java \
    --endpoint "<대상 API 경로>" \
    --ddl-path db/00-init-schema.sql
  ```

* **Output**: 인증 타입, 필수 헤더, throw 분기(에러 코드/HTTP 상태), 상태값 비교, 인터셉터 소스 원문, 인터셉터 DB 의존성
* **Action**:
  * `required_headers` → 모든 테스트의 HTTP 요청에 인증 헤더 포함
  * `throw_branches` → ATC 테스트 시나리오에 인증 실패 케이스 추가
  * `state_value_checks` → setup SQL에 인증 관련 상태값 데이터 포함
  * `auth_db_dependencies` → 인터셉터/필터가 DI로 주입받은 Service/DAO를 통해 호출하는 DB 쿼리의 테이블/컬럼 목록. 이 테이블/컬럼이 DDL에 누락되면 인증 단계에서 모든 API 호출이 실패한다.
  * `ddl_missing_tables` / `ddl_missing_columns` → DDL 교차 검증 결과. 누락 항목이 있으면 **반드시 `00-init-schema.sql`에 해당 테이블/컬럼을 추가**한 후 다음 단계로 진행한다. 이 검증을 건너뛰면 인증 쿼리가 런타임에 SQL exception을 던져 모든 테스트가 인증 에러로 실패한다.
  * `llm_analysis_required: true`이면 `interceptor_sources`의 소스 원문을 분석하여:
    - URL 세그먼트 검증 규칙 파악
    - DB 인증 조회 패턴 파악
    - 테스트 데이터 INSERT에 필요한 인증 테이블/컬럼 식별

* **인증 데이터 체인 명세 (Auth Data Chain Specification)**:

  로그인 플로우가 세션 기반인 경우, 다음 **End-to-End 데이터 체인**을 반드시 추적하여 setup SQL과 login 헬퍼가 일관되도록 합니다:

  | 단계 | 확인 항목 | 예시 |
  |------|---------|------|
  | ① DB 저장 형식 | 비밀번호 인코딩 prefix (`{pbkdf2}`, `{bcrypt}`, `{noop}`) | `PasswordEncoderConfig` 확인 |
  | ② 로그인 API 파라미터 | raw 비밀번호? 해시 비밀번호? | `passwordEncoder.matches(rawPw, stored)` |
  | ③ 로그인 성공 조건 | fail count 초기화, token 초기화, 비밀번호 만료 회피 | `LOGIN_TOKEN=NULL`, `PW_UPT_DATE=NOW()` |
  | ④ 세션 저장 키 | 인터셉터가 검증하는 세션 속성 | `loginToken`, `loginClientSeq` |
  | ⑤ 중복 로그인 방지 | DB token vs 세션 token 비교 로직 | `checkBeforeLogin` → `LOGIN_TOKEN` 비어있어야 신규 발급 |
  | ⑥ 쿠키 전달 | Spring Session 쿠키 이름 (`SESSION`, `JSESSIONID`) | `spring.session.store-type` 확인 |
  | ⑦ 인터셉터 검증 | 요청 헤더 요구사항 (`X-Requested-With`) | exception handler의 JSON/HTML 분기 조건 |

  **Action**: setup SQL 생성 시 ①~⑤를 반영하고, login 헬퍼 코드에서 ②⑥을 반영하며, API 호출 시 ⑦을 반영합니다.
  이 체인의 어느 한 곳이라도 불일치하면 302 리다이렉트 또는 HTML 에러 응답이 발생합니다.

#### Step 3-C: External Dependency Analysis (WireMock Only)

* **Execute**:
  `skills/detect-external-calls --include-di-analysis` — DI 기반 외부 의존성 + non-DI 외부 연동을 한 번에 탐지
  ```bash
  python3 skills/detect-external-calls/main.py --file <CONTROLLER_FILE> --method <HANDLER_METHOD> --include-di-analysis
  ```
  * DI 기반: 생성자 주입, `@Autowired` 등으로 주입된 외부 의존성
  * non-DI: `new` 인스턴스, static 호출, 직접 HTTP 클라이언트

* **Classify all dependencies** (from skill output):

  **✅ DO NOT Mock** — Internal application layers:
  * Service classes, Repository/Mapper interfaces, JPA/MyBatis components, DAO, internal utilities
  * 이들은 실제 서버에서 실제 구현체로 동작한다.

  **❌ MUST Stub via WireMock** — External integration services:

  | Category | Examples |
  |----------|----------|
  | HTTP/REST clients | `RestTemplate`, `WebClient`, `FeignClient`, custom API adapters |
  | Message queues | `KafkaTemplate`, `RabbitTemplate`, `JmsTemplate` |
  | External adapters | `CASAdapter`, `PaymentGatewayAdapter`, `SmsApiClient` |
  | Cloud SDKs | `AmazonS3`, `SqsTemplate`, Google/Azure clients |

  **Decision rule**: "Does this component make network calls outside the application?" → If yes, stub it with WireMock.

* **외부 연동 처리 전략** (WireMock 우선, fallback 포함):

  | `detect-external-calls` pattern | Strategy |
  |--------------------------------|----------|
  | `new_instance` (wiremock_applicable: true) | ✅ WireMock stub — 대상 URL을 WireMock 서버로 변경 |
  | `http_client` (wiremock_applicable: true) | ✅ WireMock stub — 직접 HTTP 클라이언트의 대상 URL을 스텁 |
  | DI 기반 외부 서비스 (`--include-di-analysis` 출력) | ✅ WireMock stub — 해당 서비스가 호출하는 외부 URL을 스텁 |
  | `new_instance` (wiremock_applicable: false) | ⚠️ 내부 구현 추적 → HTTP 호출이 있으면 해당 URL을 WireMock으로 스텁. HTTP 기반이 아니면 아래 fallback 적용 |
  | `static_call` (wiremock_applicable: false) | ⚠️ 내부 구현 추적 → HTTP 호출이 있으면 해당 URL을 WireMock으로 스텁. HTTP 기반이 아니면 아래 fallback 적용 |

  **Fallback 전략** (`wiremock_applicable: false` + HTTP 기반이 아닌 경우):
  1. 테스트 서버를 테스트 전용 프로파일로 기동하여 해당 외부 연동을 비활성화하거나 dummy 응답을 반환하도록 설정
  2. 해당 외부 연동이 테스트 시나리오에 영향을 주지 않는 경우, 테스트에서 무시하고 주석으로 "사전 조건: 서버 측 설정 필요" 표기
  3. 테스트 코드에서는 절대 `mockStatic()`, `MockedConstruction<T>`, `@MockBean` 등 내부 모킹을 사용하지 않는다
  4. **하드코딩 URL HTTP 호출** (프로퍼티로 URL 변경 불가): 서버 코드에서 외부 호스트명이 소스코드에 하드코딩된 HTTP 호출이 테스트 실행 경로에 포함되는 경우, WireMock은 localhost만 가로챌 수 있으므로 mock 처리가 불가능하다. 이 경우 해당 경로를 거치는 테스트 케이스는 생성하지 않고, 테스트 플랜 산출물의 '테스트 불가 케이스'에 사유를 기재한다. 서버 측에서 해당 URL을 프로퍼티로 외부화한 후 테스트를 추가할 수 있다.

  **전제 조건**: 대상 서버의 외부 API URL 설정을 WireMock 주소로 변경할 수 있어야 한다 (환경 변수, 프로퍼티 파일 등).

* **WireMock stub은 JSON 파일로 외부화**:

  WireMock 매핑은 테스트 코드에 inline으로 작성하지 않고, JSON 파일로 분리한다.

  **파일 네이밍**: `src/test/resources/wiremock/{xxx}-test.json` (`{xxx}`는 테스트 대상 API 또는 외부 연동 식별자)

  ```json
  // src/test/resources/wiremock/cas-auth-test.json
  {
    "request": {
      "method": "POST",
      "urlPath": "/external/cas/auth",
      "headers": {
        "Content-Type": { "equalTo": "application/json" }
      }
    },
    "response": {
      "status": 200,
      "headers": { "Content-Type": "application/json" },
      "jsonBody": { "resultCode": "0000" }
    }
  }
  ```

* **WireMock setup pattern** (JSON 파일 로딩):

  ```java
  private static WireMockServer externalApiStub;

  @BeforeAll
  static void startStubs() {
      externalApiStub = new WireMockServer(wireMockConfig().port(9090));
      externalApiStub.start();
  }

  @AfterAll
  static void stopStubs() {
      externalApiStub.stop();
  }

  // 병렬 안전: resetAll() 금지 — 각 메서드가 개별 stub을 등록/삭제
  // @BeforeEach에서 resetAll()을 호출하면 다른 병렬 메서드의 stub을 삭제함

  private static String loadWireMockStub(WireMockServer server, String resourcePath) {
      try (InputStream is = ControllerBlackBoxTest.class
              .getClassLoader().getResourceAsStream(resourcePath)) {
          String json = new String(is.readAllBytes(), StandardCharsets.UTF_8);
          StubMapping stub = StubMapping.buildFrom(json);
          server.addStubMapping(stub);
          return stub.getId().toString();
      } catch (Exception e) {
          throw new RuntimeException("WireMock stub loading failed: " + resourcePath, e);
      }
  }

  private static void removeWireMockStubs(WireMockServer server, List<String> stubIds) {
      for (String id : stubIds) {
          if (id == null) continue;
          try { server.removeStubMapping(server.getStubMapping(java.util.UUID.fromString(id)).getItem()); }
          catch (Exception ignored) {}
      }
  }

  // 호출 검증
  externalApiStub.verify(postRequestedFor(urlPathEqualTo("/external/cas/auth")));
  ```

* **OTEL Baggage 기반 WireMock stub 격리 (병렬 안전)**:

  `@Execution(CONCURRENT)` 병렬 실행 시, 같은 URL 패턴의 stub이 여러 테스트에서 동시에 등록/삭제되면 경쟁 조건이 발생합니다.
  이를 해결하기 위해 **OTEL Baggage의 `tid` (test-id)**를 이용하여 stub을 테스트 메서드별로 격리합니다.

  **동작 원리**:
  1. 테스트 코드가 API 호출 시 `baggage: tid=<고유값>` 헤더를 포함
  2. 앱 서버의 OTEL Java Agent가 baggage를 context로 파싱
  3. 앱 서버가 외부 연동(WireMock) 호출 시, OTEL Agent가 자동으로 `baggage: tid=<고유값>` 를 outgoing 요청에 inject
  4. WireMock stub이 `"baggage": { "contains": "tid=<고유값>" }` 으로 매칭하여 해당 테스트 전용 응답 반환

  **전제 조건**:
  - 앱 서버에 OTEL Java Agent 부착 (`-javaagent:opentelemetry-javaagent.jar`)
  - propagator에 baggage 포함 (기본값): `OTEL_PROPAGATORS=tracecontext,baggage`
  - exporter 비활성화 (테스트 목적이므로): `OTEL_TRACES_EXPORTER=none`, `OTEL_METRICS_EXPORTER=none`

  **WireMock stub JSON 예시** (baggage tid 매칭):
  ```json
  {
    "request": {
      "method": "POST",
      "urlPathPattern": "/ncas/auth.*",
      "headers": {
        "baggage": { "contains": "tid=T000084094" }
      }
    },
    "response": {
      "status": 200,
      "headers": { "Content-Type": "application/json" },
      "jsonBody": { "RESPCODE": "00000000", "RESPMSG": "SUCCESS" }
    }
  }
  ```

  **테스트 코드 패턴**:
  ```java
  @Test
  void NTC_someTest(TestInfo info) throws Exception {
      String tid = testId(info);  // 메서드별 고유값

      // WireMock stub 등록 (tid 매칭 조건 포함)
      String stubJson = loadStubTemplate("wiremock/ncas-auth.json")
              .replace("${TID}", tid);
      String stubId = registerWireMockStub(stubJson);

      try {
          // API 호출 시 baggage 헤더 포함
          given()
              .header("baggage", "tid=" + tid)
              .header("X-Requested-With", "XMLHttpRequest")
              // ...
              .post("/web/admin/members");
      } finally {
          removeWireMockStub(stubId);
      }
  }
  ```

  이 패턴으로 각 테스트 메서드는 자신만의 stub에만 매칭되며, 병렬 실행 시 다른 테스트의 stub과 간섭하지 않습니다.

* **WireMock stub 응답 ↔ 서버 파싱 코드 교차 검증 (CRITICAL)**:

  WireMock stub JSON 작성 후, 서버가 해당 응답을 파싱하는 코드와 **필드 단위 교차 검증**을 수행합니다.
  이 검증을 건너뛰면 stub이 올바른 HTTP 200을 반환해도 서버가 필드 누락/타입 불일치로 예외를 던집니다.

  **검증 절차**:
  1. `detect-external-calls`의 call chain에서 최종 HTTP 응답을 파싱하는 클래스/메서드를 식별
  2. 해당 코드에서 `resp.getString("X")`, `resp.get("X")`, `resp.optString("X")` 호출을 수집
  3. 수집된 필드명이 WireMock stub의 `jsonBody`에 **동일한 키명(대소문자 포함)**으로 존재하는지 확인
  4. `.endsWith("0000")`, `.substring(length-4)` 등 값 형식 검증 코드가 있으면 stub 값이 해당 형식을 충족하는지 확인
  5. 조건부 필드 (`if (resp.has("X"))`)는 성공 시나리오 stub에 포함, 실패 시나리오 stub에서는 제외

  **불일치 예시와 증상**:

  | stub 필드 | 서버 기대 | 증상 |
  |-----------|---------|------|
  | `access_token` (소문자) | `ACCESS_TOKEN` (대문자) | 토큰 null → 인증 실패 |
  | `RESPCODE` 누락 | `resp.optString("RESPCODE")` → 빈 문자열 | `.substring(len-4)` → IndexOutOfBounds |
  | `RESULT` 누락 | `resp.getString("RESULT")` | JSONException → catch → "9999" 반환 |

  **WireMock stub 작성 규칙**:
  - stub의 `jsonBody` 키명은 **서버 파싱 코드의 문자열 리터럴과 정확히 동일** (대소문자 포함)
  - 성공 응답 stub: 서버가 `has()` 또는 `getString()`으로 읽는 모든 필수 필드를 포함
  - 값의 길이/형식: 서버의 `substring`, `endsWith` 등 조건을 충족하는 값 사용 (예: `"00000000"` — 8자리, 끝 4자리가 `"0000"`)
  - `detect-external-calls`의 `response_schema` 출력이 있으면 이를 기준으로 stub 생성

* **docker-compose.yml 환경 변수 업데이트**:

  `detect-external-calls`에서 WireMock 대상으로 식별된 외부 연동의 URL 프로퍼티는,
  `docker/docker-compose.yml`의 `app` 서비스 `environment` 섹션에 반영한다.
  Docker Compose 네트워크 내에서는 서비스명으로 접근하므로 `localhost` 대신 `wiremock`을 사용한다.

  ```yaml
  app:
    environment:
      # detect-external-calls 결과 반영
      CASURL: http://wiremock:8080/NIF/casnif.jsp
      APIM.PRIVATE.HOST: http://wiremock
      APIM.PRIVATE.PORT: 8080
  ```

  이 작업은 테스트케이스 생성 과정에서 외부 연동이 새로 발견될 때마다 누적 반영한다.
  기존에 설정된 환경 변수는 수정하지 않고, 새로 발견된 것만 추가한다.

#### Step 3-D: Database State & Test Data (Data Preparation via JDBC)

**CRITICAL**: This step generates ACTUAL SQL files executed against the real database via direct JDBC.

* **Execute**:
  1. `skills/analyze-test-data --controller` — dependency-tree 산출물을 읽어 관련 Mapper XML을 자동 탐색 후 분석
     ```bash
     python3 nimbus-skills/analyze-test-data/main.py \
       --controller <CONTROLLER_FILE> --method <HANDLER_METHOD> --project-root .
     ```
     * 전제: Step 0에서 `dependency-tree` 산출물(`.nimbus/artifacts/dependency-tree/{ControllerName}_{method}.md`)이 생성되어 있어야 함
     * 출력: 관련 테이블/컬럼 매핑, WHERE 조건, JOIN 관계, 동적 SQL 분기, FK 관계
     * `methods_traced` 필드에 컨트롤러가 실제 호출하는 Mapper 메서드 목록이 있으므로, `mapper_analyses`에서 해당 메서드가 사용하는 테이블만 setup SQL 대상으로 선정
  2. DB fixture가 필요하면 `analyze-test-data/generate_db_fixtures.py`를 사용 (같은 스킬 내 포함)
     ```bash
     python3 nimbus-skills/analyze-test-data/generate_db_fixtures.py --file <ENTITY_OR_MAPPER_FILE>
     ```

* **⚠️ 인터셉터/필터 DB 의존성 반영 (CRITICAL)**:
  Step 3-B(`analyze-auth-flow`)의 `auth_db_dependencies` 출력에서 인터셉터가 사용하는 테이블/컬럼을 확인하고,
  이를 setup SQL 생성 범위에 반드시 포함한다. 인터셉터는 Controller의 DI 체인 밖에 있으므로
  `analyze-test-data`가 자동으로 추적하지 못하는 사각지대이다.

  구체적으로:
  - `auth_db_dependencies[].tables` → setup SQL에 해당 테이블의 INSERT 포함
  - `auth_db_dependencies[].columns` → INSERT 시 해당 컬럼에 유효한 값 포함
  - `ddl_missing_tables` / `ddl_missing_columns` → DDL(`00-init-schema.sql`)에 누락된 테이블/컬럼 추가
  - 인터셉터가 조회하는 인증 데이터(API 키, 접근 토큰, 파트너 정보 등)가 setup SQL에 포함되어야 인증을 통과할 수 있다

* **Generate SQL files**:

  * `src/test/resources/sql/{api-kebab}-setup.sql` — Insert test data (parents before children)
  * `src/test/resources/sql/{api-kebab}-teardown.sql` — Delete test data (children before parents)

  `{api-kebab}`는 URL 경로 기반 API 식별자 (예: `realtime-pin-check`, `realtime-pin-regist`)

  **⚠️ API별 완전 독립 원칙 (CRITICAL)**:

  각 API 테스트의 setup/teardown SQL은 **다른 API 테스트 파일과 아무것도 공유하지 않는다**.
  같은 Controller의 다른 엔드포인트라도 setup SQL을 공유하지 않는다.

  각 setup SQL에는 해당 API 테스트에 필요한 **모든 데이터**를 포함한다:
  - 인터셉터 인증 데이터 (TB_OPEN_API_AUTH, TB_PARTNER 등)
  - 권종/요금제 마스터 데이터 (TB_MAIN, TB_POSSIBLE_PLAN 등)
  - 해당 API 전용 테스트 데이터 (TB_ISSUE_LEDGER, TB_GIFT_NO_INFO 등)

  **ID 충돌 방지**: 같은 Controller의 다른 API 테스트와 동시 실행될 수 있으므로,
  각 API 테스트는 고유한 ID 범위를 사용한다 (예: pinCheck는 PARTNER_SEQ=9101, pinRegist는 9201).

  ```sql
  -- {api-kebab}-setup.sql: 해당 API에 필요한 모든 데이터 (인증 포함)
  INSERT INTO TB_PARTNER (PARTNER_SEQ, ...) VALUES (9101, ...);
  INSERT INTO TB_OPEN_API_AUTH (API_ID, ...) VALUES ('DGFT_RT_pincheck', ...);
  INSERT INTO TB_MAIN (GIFT_CODE, ...) VALUES ('GC_RT_001', ...);
  INSERT INTO TB_ISSUE_LEDGER (...) VALUES (...);

  -- {api-kebab}-teardown.sql: 역순 삭제
  DELETE FROM TB_ISSUE_LEDGER WHERE ISSUE_NO IN (...);
  DELETE FROM TB_MAIN WHERE GIFT_CODE IN ('GC_RT_001');
  DELETE FROM TB_OPEN_API_AUTH WHERE API_ID IN ('DGFT_RT_pincheck');
  DELETE FROM TB_PARTNER WHERE PARTNER_SEQ IN (9101);
  ```

* **SQL requirements**:
  * Use explicit IDs (not auto-increment) for predictable data
  * Include ALL NOT NULL columns, respect FK/UNIQUE constraints
  * Use realistic values matching production constraints
  * Ensure idempotency (DELETE before INSERT if needed)
  * 각 API 테스트의 SQL은 자기 완결적 — 단독 실행 시 모든 데이터가 준비됨
  * **컬럼 길이 준수**: 테스트 데이터의 문자열 값(loginId, 이름, 코드 등)은 DDL의 해당 컬럼 `varchar(N)` 길이를 초과하지 않아야 한다. `${PLACEHOLDER}` 치환 후 최대 길이를 계산하여 확인한다 (예: `"prefix_" + 9자리숫자` = 16자 → `varchar(15)` 컬럼에는 부적합).

* **실행 방식**: `@Sql` 어노테이션 대신 테스트 코드에서 직접 JDBC로 실행하는 유틸리티 메서드 사용.

### Step 4: Test Code Generation (Final Assembly)

Using context from Steps 0-3, generate the black-box integration test.

**⚠️ 테스트 플랜 산출물 대조 (CRITICAL)**:

Step 2에서 생성된 `.nimbus/artifacts/test-plan/{ClassName}_{MethodName}.md` 산출물의 체크리스트를 반드시 1:1 대조한다.
Step 2-B에서 생성된 `.nimbus/artifacts/vtc-plan/{ClassName}_{MethodName}.md` 산출물의 VTC 케이스도 함께 대조한다.
산출물에 있는 모든 테스트 케이스 ID가 생성되는 테스트 코드에 대응하는 메서드로 존재해야 한다.
산출물에 없는 케이스를 추가하는 것은 허용하지만, 산출물에 있는 케이스를 빠뜨리는 것은 금지한다.

**⚠️ Assertion 품질 규칙 (CRITICAL)**:

NTC(Happy Path) assertion은 setup SQL에 삽입한 테스트 데이터와 정확히 대응해야 한다.

| ❌ 금지 (Weak Matcher) | ✅ 필수 (Exact Matcher) | 이유 |
|------------------------|------------------------|------|
| `.body("Name", notNullValue())` | `.body("Name", equalTo("TestGift 1GB"))` | setup SQL의 GIFT_NAME과 1:1 대응 |
| `.body("List.size()", greaterThanOrEqualTo(1))` | `.body("List.size()", equalTo(2))` | setup SQL에서 2건 삽입했으므로 정확히 2 |
| `.body("Count", not(empty()))` | `.body("Count", equalTo("2"))` | 타입(String/Integer)까지 정확히 비교 |
| `.body("Date", notNullValue())` | `.body("Date", matchesPattern("\\d{4}-\\d{2}-\\d{2}"))` | 동적 값은 포맷 검증 |
| `.statusCode(anyOf(is(200), is(500)))` | `.statusCode(200)` | 응답 코드는 정확히 하나만 기대 |
| `.body("ResultCode", anyOf(equalTo("1007"), equalTo("1013")))` | `.body("ResultCode", equalTo("1007"))` | 에러 코드도 정확히 하나만 기대 |

**`anyOf` matcher 절대 금지 (CRITICAL)**:

`anyOf()`는 "여러 값 중 아무거나 와도 통과"하는 느슨한 matcher이다. 이는 서버 동작을 정확히 파악하지 못했다는 의미이므로 테스트로서 가치가 없다.

- `statusCode(anyOf(is(200), is(400), is(500)))` → 서버가 실제로 반환하는 HTTP 상태 코드를 확인하고 `statusCode(500)` 처럼 정확히 하나만 기대
- `.body("ResultCode", anyOf(equalTo("1007"), equalTo("1013")))` → 서버가 실제로 반환하는 에러 코드를 확인하고 `equalTo("1007")` 처럼 정확히 하나만 기대
- `not(equalTo("0000"))` → 실제 에러 코드를 확인하고 `equalTo("3032")` 처럼 구체적 값으로 검증

테스트 작성 시 서버 응답이 불확실하면, 먼저 한 번 호출하여 실제 응답을 확인한 후 정확한 값으로 assertion을 작성한다.

리스트 응답 검증 패턴:
```java
// ❌ BAD: 아무 값이나 와도 통과
.body("CampaignList[0].GiftName", notNullValue())
.body("CampaignList.size()", greaterThanOrEqualTo(1))

// ✅ GOOD: setup SQL 데이터와 정확히 매칭
.body("CampaignList.size()", equalTo(2))
.body("CampaignList.find { it.IssueNo == 'TEST_ISSUE_001' }.GiftName", equalTo("TestGift 1GB"))
.body("CampaignList.find { it.IssueNo == 'TEST_ISSUE_001' }.DataSize", equalTo("1024"))
```

`notNullValue()`는 응답 구조 존재 여부 확인이 아닌, 필드 값 검증 목적으로는 절대 사용하지 않는다.

```java
/**
 * Black-Box API Integration Test
 *
 * @target-controller {ControllerClassName} ({Controller 파일 경로})
 * @target-api {HTTP_METHOD} {API_URL_PATH}
 */
// 내부 클래스 import 없음
@TestInstance(TestInstance.Lifecycle.PER_METHOD)
@Execution(ExecutionMode.CONCURRENT)
class PascalCaseBlackBoxTest {

    private static final String BASE_URL = System.getProperty("test.server.url", "http://localhost:8080");

    private static final String DB_URL = System.getProperty("test.db.url", "jdbc:mariadb://localhost:3306/dgift");
    private static final String DB_USER = System.getProperty("test.db.user", "test");
    private static final String DB_PASSWORD = System.getProperty("test.db.password", "test");

    private static final String WIREMOCK_URL = System.getProperty("test.wiremock.url", "http://localhost:9090");

    // 병렬 안전: 각 SQL 실행마다 독립 Connection을 사용
    private static Connection getDbConnection() throws Exception {
        return DriverManager.getConnection(DB_URL, DB_USER, DB_PASSWORD);
    }


    // 메서드별 고유 ID 생성 (결정적 — 메서드명 기반)
    private String testId(TestInfo info) {
        return String.format("T%09d",
            Math.abs(info.getTestMethod().get().getName().hashCode()) % 1_000_000_000);
    }

    @Test
    void NTC_callAPI_success(TestInfo info) throws Exception {
        String id = testId(info);
        List<String> stubIds = new ArrayList<>();

        // Given: 방어적 teardown → setup (멱등성 보장)
        executeSqlTemplate("sql/test-teardown.sql", Map.of("ID", id));
        executeSqlTemplate("sql/test-setup.sql", Map.of("ID", id));

        try {
            // WireMock stub 개별 등록 (${TID}를 치환하여 병렬 안전)
            stubIds.add(registerStub("wiremock/cas-auth-test.json", id));

            // When: 순수 HTTP 호출 (baggage 헤더로 tid 전파)
            RestAssured.given().log().all()
                .baseUri(BASE_URL)
                .header("baggage", "tid=" + id)
                .header("X-Requested-With", "XMLHttpRequest")
                .contentType(ContentType.JSON)
                .body("{\"param1\":\"" + id + "\",\"param2\":\"value2\"}")
            .when()
                .post("/api/endpoint")
            .then().log().all()
                .statusCode(200)
                .body("ResultCode", equalTo("0000"));

            // Then: DB 상태 검증 (직접 JDBC — 독립 Connection)
            try (Connection conn = getDbConnection();
                 PreparedStatement ps = conn.prepareStatement(
                    "SELECT status FROM TB_ORDER WHERE order_id = ?")) {
                ps.setString(1, id);
                ResultSet rs = ps.executeQuery();
                assertTrue(rs.next());
                assertEquals("COMPLETED", rs.getString("status"));
            }

        } finally {
            // Teardown: stub 개별 삭제 + 이 테스트만의 데이터 정리
            removeStubs(stubIds);
            executeSqlTemplate("sql/test-teardown.sql", Map.of("ID", id));
        }
    }

    private static void executeSqlTemplate(String resourcePath, Map<String, String> params) {
        try (Connection conn = getDbConnection();
             InputStream is = PascalCaseBlackBoxTest.class
                .getClassLoader().getResourceAsStream(resourcePath);
             Statement stmt = conn.createStatement()) {
            String sql = new String(is.readAllBytes(), StandardCharsets.UTF_8);
            for (var entry : params.entrySet()) {
                sql = sql.replace("${" + entry.getKey() + "}", entry.getValue());
            }
            for (String line : sql.split("\n")) {
                String trimmed = line.trim();
                if (trimmed.startsWith("--") || trimmed.isEmpty()) continue;
                if (trimmed.endsWith(";")) trimmed = trimmed.substring(0, trimmed.length() - 1);
                if (!trimmed.isEmpty()) stmt.execute(trimmed);
            }
        } catch (Exception e) {
            throw new RuntimeException("SQL template execution failed: " + resourcePath, e);
        }
    }

    /** stub JSON 템플릿을 읽어 ${TID}를 치환 후 Docker WireMock에 등록 */
    private static String registerStub(String resourcePath, String tid) {
        try (InputStream is = PascalCaseBlackBoxTest.class
                .getClassLoader().getResourceAsStream(resourcePath)) {
            String json = new String(is.readAllBytes(), StandardCharsets.UTF_8);
            json = json.replace("${TID}", tid);
            Response resp = given()
                    .baseUri(WIREMOCK_URL)
                    .contentType(ContentType.JSON)
                    .body(json)
            .when()
                    .post("/__admin/mappings");
            return resp.jsonPath().getString("id");
        } catch (Exception e) {
            throw new RuntimeException("WireMock stub registration failed: " + resourcePath, e);
        }
    }

    private static void removeStubs(List<String> stubIds) {
        for (String id : stubIds) {
            if (id == null) continue;
            try { given().baseUri(WIREMOCK_URL).delete("/__admin/mappings/" + id); }
            catch (Exception ignored) {}
        }
    }
}
```

**테스트 실행 흐름**:

```text
1. @BeforeAll → WireMock 스텁 서버 기동 + DB JDBC 연결
2. Test method → executeSql(teardown) → executeSql(setup) → stub 개별 등록 → RestAssured HTTP → 실제 서버
   → 서버 내부: Controller → Service → 외부 호출 → WireMock 응답 → DB 쿼리 (REAL)
3. Assertions: HTTP 응답 검증 + JDBC로 DB 상태 검증 + WireMock verify()
4. finally → stub 개별 삭제 + executeSql(teardown)
```

**Generated files** (엔드포인트별 1세트씩 생성):

1. `src/test/java/.../{PascalCase}BlackBoxTest.java` — 엔드포인트별 독립 테스트 파일 (예: `RealtimePinCheckBlackBoxTest.java`)
2. `src/test/resources/sql/{api-kebab}-setup.sql` — 해당 API에 필요한 모든 데이터 (인증 포함, 자기 완결적)
3. `src/test/resources/sql/{api-kebab}-teardown.sql` — 해당 API 데이터 전체 삭제
4. `src/test/resources/wiremock/{api-kebab}-{external}-{scenario}.json` (외부 연동별 1개 이상)

**파일 네이밍 규칙 (URL 경로 기반)**:

URL에서 path variable(`{xxx}`)과 와일드카드(`**`) 제거 후, 세그먼트를 연결한다.
예: `/realtime/pin/check` → `realtime-pin-check` (kebab-case), `RealtimePinCheck` (PascalCase)

| 유형 | 네이밍 패턴 | 예시 (`/realtime/pin/check`) |
|------|-------------|------------------------------|
| Java | `{PascalCase}BlackBoxTest.java` | `RealtimePinCheckBlackBoxTest.java` |
| Setup SQL | `{kebab-case}-setup.sql` | `realtime-pin-check-setup.sql` |
| Teardown SQL | `{kebab-case}-teardown.sql` | `realtime-pin-check-teardown.sql` |
| WireMock | `{kebab-case}-{external}-{scenario}.json` | `realtime-pin-check-cas-success.json` |
| Test Plan | `{ControllerName}_{method}.md` | `DataGiftRealtimeRegController_pinCheck.md` |
| Dependency Tree | `{ControllerName}_{method}.md` | `DataGiftRealtimeRegController_pinCheck.md` |

**⚠️ API별 독립성 원칙**:

같은 Controller의 다른 엔드포인트 테스트와 setup/teardown SQL을 공유하지 않는다.
각 테스트 파일은 자기 setup SQL만으로 단독 실행 가능해야 한다.
이를 위해 인증 데이터(TB_PARTNER, TB_OPEN_API_AUTH 등)도 각 API의 setup SQL에 개별 포함한다.
ID 범위를 API별로 분리하여 동시 실행 시 충돌을 방지한다.

**⚠️ 병렬 안전 테스트 데이터 격리 (CRITICAL)**:

테스트 메서드는 `@Execution(CONCURRENT)`로 병렬 실행될 수 있어야 한다. 이를 위해 **각 테스트 메서드는 자신만의 고유 데이터 세트를 가지며, 다른 테스트와 데이터를 공유하지 않는다.**

**원칙**:
1. `@BeforeAll`/`@AfterAll`에서 공통 테스트 데이터를 INSERT/DELETE하지 않는다
2. 각 `@Test` 메서드가 자신의 데이터를 직접 setup하고 직접 teardown한다
3. 모든 테스트 데이터의 PK/식별자는 메서드별로 고유해야 한다

**고유 ID 생성 규칙**:
- 테스트 메서드명으로부터 결정적(deterministic) ID를 생성한다 (재실행 시 동일값 보장)
- 접두사 `T`를 붙여 실 데이터와 구별한다
- 예: `testId(info)` → `"T000384721"` (메서드명 해시 기반 9자리)

**API 요청 파라미터의 고유성 (CRITICAL)**:
- DB PK뿐 아니라, 서버가 **unique 검증하는 요청 파라미터**(CTN, email, loginId 등)도 `testId` 기반으로 고유하게 생성한다.
- VTC/ATC 테스트라도 "검증 실패로 빨리 반환될 것"이라는 가정으로 하드코딩하지 않는다.
- 이유: 병렬 실행 시 서버의 중복 체크 쿼리가 다른 테스트의 데이터와 충돌할 수 있다.
- 예: CTN → `"010" + testId(info).substring(1, 9)` (메서드마다 다른 전화번호)

**코드 패턴**:
```java
@TestInstance(TestInstance.Lifecycle.PER_METHOD)
@Execution(ExecutionMode.CONCURRENT)
class XxxBlackBoxTest {

    // 메서드별 고유 ID 생성 (결정적 — 메서드명 기반)
    private String testId(TestInfo info) {
        return String.format("T%09d",
            Math.abs(info.getTestMethod().get().getName().hashCode()) % 1_000_000_000);
    }

    @Test
    void NTC_normalOrder(TestInfo info) throws Exception {
        String id = testId(info);
        Map<String, String> params = Map.of("CUST_ID", id, "ORDER_ID", "ORD" + id);

        // 방어적 teardown → setup (멱등성 보장)
        executeSqlTemplate("sql/order-teardown.sql", params);
        executeSqlTemplate("sql/order-setup.sql", params);

        try {
            given().baseUri(BASE_URL)
                .param("custId", id)
            .when().post("/api/order")
            .then().statusCode(200)
                .body("ResultCode", equalTo("0000"));
        } finally {
            executeSqlTemplate("sql/order-teardown.sql", params);
        }
    }
}
```

**SQL 템플릿 파일 규칙**:
- setup/teardown SQL에서 고유해야 하는 값은 `${PLACEHOLDER}` 형식의 플레이스홀더를 사용한다
- 플레이스홀더는 Java 코드에서 `testId()` 기반 값으로 치환된다
- FK 관계가 있는 테이블은 동일한 플레이스홀더 체계를 일관되게 사용한다
- **컬럼 길이 검증**: 각 INSERT 값이 DDL의 `varchar(N)` 제한 이내인지 확인한다. `${PLACEHOLDER}` 치환 후 최대 길이 기준으로 계산한다 (예: `"prefix_" + 9자리` = 16자 → `varchar(15)` 부적합).

```sql
-- src/test/resources/sql/order-setup.sql
INSERT INTO TB_CUSTOMER (CUST_ID, CUST_NM, STATUS) VALUES ('${CUST_ID}', 'TestUser', 'ACTIVE');
INSERT INTO TB_ORDER (ORDER_ID, CUST_ID, AMOUNT) VALUES ('${ORDER_ID}', '${CUST_ID}', 1000);
```

```sql
-- src/test/resources/sql/order-teardown.sql
DELETE FROM TB_ORDER WHERE ORDER_ID = '${ORDER_ID}';
DELETE FROM TB_CUSTOMER WHERE CUST_ID = '${CUST_ID}';
```

**SQL 템플릿 실행 유틸리티**: 위 `PascalCaseBlackBoxTest` 예제의 `executeSqlTemplate(String, Map)` 정의를 그대로 사용한다 — 병렬 안전을 위해 매 호출마다 `getDbConnection()`으로 독립 Connection을 연다.

**금지 사항**:
- `@BeforeAll`에서 테스트 데이터 INSERT 금지 (WireMock/DB 연결 초기화만 허용)
- 여러 테스트 메서드가 동일한 PK 값을 사용하는 것 금지
- `@AfterAll`에서 `DELETE WHERE id IN (...)` 같은 일괄 삭제 금지 (각 메서드가 자기 데이터만 정리)

### Step 5: Test Code Validation (정적 검증)

* **Execute**: `skills/validate-test-code` — 생성된 테스트 코드와 리소스를 빌드 전에 정적 검증
  ```bash
  python3 skills/validate-test-code/main.py \
    --java-file src/test/java/.../ControllerBlackBoxTest.java \
    --wiremock-dir src/test/resources/wiremock \
    --sql-dir src/test/resources/sql
  ```

* **검증 항목**:
  * 금지 키워드: `@MockBean`, `@Mock`, `mockStatic`, `stubFor(`, `@SpringBootTest`, `@Autowired` 등
  * 추가 금지: `@Transactional` (BlackBox 테스트에서는 트랜잭션 롤백이 아닌 명시적 teardown SQL 사용)
  * 내부 클래스 import: `import kr.co.uplus.dgift.*` 패턴 탐지
  * WireMock JSON 유효성: JSON 파싱 + `request`/`response` 키 존재
  * SQL 파일 존재: setup/teardown SQL 유무

* **Action**:
  * 검증 통과 (exit 0) → Step 6으로 진행
  * 검증 실패 (exit 1) → 위반 사항 수정 후 재검증

### Step 6: Test Execution, Self-Correction & Coverage Augmentation

> **상세 실행 절차, JaCoCo 커버리지 측정 루프, 보강 기준은 `test-execution.md`를 참조한다.**
> 이 문서에서는 테스트 코드 생성까지(Step 5)의 규칙을 정의하고, 실행/보강 루프는 `test-execution.md`에 위임한다.

테스트 코드 생성 완료 후 반드시 다음을 확인:

* **테스트 플랜 산출물 대조**: `.nimbus/artifacts/test-plan/{ClassName}_{MethodName}.md`의 체크리스트를 1:1 대조하여 모든 항목에 대응하는 테스트 메서드가 존재하는지 확인한다.
* **VTC 변조 산출물 대조**: `.nimbus/artifacts/vtc-plan/{ClassName}_{MethodName}.md`의 VTC 케이스가 테스트 코드에 모두 반영되었는지 확인한다.
* `generate-test-plan` 산출물의 모든 `exception_paths[]` 항목이 테스트 메서드에 매핑되는지 확인한다.
* `generate-test-plan` 산출물의 모든 RED 파라미터에 대해 VTC/ATC 테스트 케이스가 존재하는지 확인한다.
* 산출물에 있는 케이스를 빠뜨리는 것은 금지한다.

---

## 📌 Test Naming & Coverage Rules

All test methods MUST use the naming convention `PREFIX_{api}_{scenario}`:

| Prefix | Purpose | Required Coverage |
|--------|---------|-------------------|
| `NTC_` | Happy Path | ≥3 success cases (200 OK) with DB state verification. 서로 다른 입력 조합, 경계값, 또는 다른 정상 시나리오로 구성 |
| `VTC_` | Validation | VTC 표준 rule 기반: `VTC_{paramName}_{RULE}` 형식. NULL, EMPTY는 RED 전체, WHITE_SPACE/SPECIAL_CHAR/LENGTH_EXCEEDED/LENGTH_SHRUNK/INVALID_FORMAT은 조건부 적용. `generate-vtc-from-red-params` 스킬로 자동 생성 |
| `ATC_` | Exception | Business exceptions: 404 Not Found, 409 Conflict, etc. |

---

## 🔧 테스트 실행 요구사항

테스트 실행 전 다음이 준비되어야 한다:

1. **Docker 환경 기동**: Pre-Step에서 생성한 `docker/docker-compose.yml`로 전체 환경이 기동되어 있어야 함
   ```bash
   docker compose -f docker/docker-compose.yml up -d
   ```
   이 명령으로 app(JaCoCo agent 포함) + DB + WireMock + LocalStack이 모두 기동된다.
   app 서비스의 health check가 healthy가 될 때까지 대기한다.

2. **외부 API URL 오버라이드**: `docker-compose.yml`의 app 서비스 `environment` 섹션에서 외부 연동 URL이 Docker 네트워크 내 WireMock 서비스(`wiremock:8080`)를 가리키도록 설정. Step 3-C에서 새로운 외부 연동이 발견될 때마다 누적 반영한다.

3. **테스트 실행**: Docker 환경이 기동된 상태에서 `jacoco-server` 스킬의 `--docker-compose` 모드를 사용한다. app 컨테이너 기동 → 테스트 실행 → app 종료 → JaCoCo 리포트 생성을 한 번에 수행한다. 상세 절차는 `test-execution.md`를 참조.

   > **NOTE**: 시스템 프로퍼티의 URL/포트는 `docker-compose.yml`의 포트 매핑과 일치해야 한다.
   > app: `8080:8080`, DB: `13306:3306`, WireMock: `9090:8080` 등.

---

## ✅ Pre-Delivery Checklist

Before delivering test code, verify ALL items:

* [ ] `detect-external-calls --include-di-analysis` 실행하여 외부 연동 탐지 완료 (Step 3-C)
* [ ] 모든 외부 연동은 WireMock stub으로만 처리
* [ ] WireMock stub은 `{xxx}-test.json` 파일로 외부화 (inline stub 금지)
* [ ] SQL 파일 네이밍: `{api-kebab}-setup.sql`, `{api-kebab}-teardown.sql`
* [ ] 각 API 테스트의 setup SQL이 자기 완결적 (인증 데이터 포함, 다른 API 테스트와 SQL 공유 없음)
* [ ] 같은 Controller의 다른 API 테스트와 ID 범위가 겹치지 않음
* [ ] 각 테스트 메서드가 `TestInfo`를 받아 `testId(info)`로 고유 ID를 생성하여 사용
* [ ] `@BeforeAll`에서 테스트 데이터 INSERT 없음 (인프라 초기화만 허용)
* [ ] SQL 파일이 `${PLACEHOLDER}` 템플릿을 사용하여 메서드별 고유 데이터 생성
* [ ] 각 `@Test` 메서드가 try-finally로 자기 데이터를 정리
* [ ] DB 검증은 직접 JDBC로 수행
* [ ] SQL setup + teardown files generated with correct FK order
* [ ] SQL 픽스처의 모든 문자열 값이 DDL 컬럼 `varchar(N)` 길이 이내 (플레이스홀더 치환 후 최대값 기준)
* [ ] DB state verification after data-modifying API calls
* [ ] Tests are idempotent (can run repeatedly without side effects)
* [ ] 테스트 코드를 별도 프로젝트로 복사해도 컴파일/실행 가능
* [ ] 시스템 프로퍼티로 서버 URL, DB 정보를 외부에서 주입 가능
* [ ] `generate-test-plan` 실행하여 필수 테스트 케이스 산출물 생성 완료 (Step 2, 내부에서 extract-key-params + extract-test-scenarios 자동 호출)
* [ ] 산출물 체크리스트의 모든 항목에 대응하는 테스트 메서드 존재
* [ ] RED 파라미터 전체에 대해 VTC/ATC 테스트 케이스 존재
* [ ] RED 파라미터에 VTC 표준 rule(NULL, EMPTY 필수 + 조건부 WHITE_SPACE/SPECIAL_CHAR/LENGTH_EXCEEDED/LENGTH_SHRUNK/INVALID_FORMAT) 적용 확인 (`generate-vtc-from-red-params` 스킬 산출물 대조)
* [ ] All values (field names, URLs, types) sourced from skill outputs, not hallucinated
* [ ] NTC/VTC/ATC 네이밍 규칙 준수
* [ ] NTC assertion에 `notNullValue()`, `not(empty())`, `greaterThanOrEqualTo()` 등 느슨한 matcher가 없음 — 모든 필드를 setup SQL 값과 `equalTo()`로 비교
* [ ] `anyOf()` matcher가 단 하나도 없음 — statusCode, body 모두 정확히 하나의 값으로 검증 (`anyOf`, `not(equalTo(...))` 금지)
* [ ] `analyze-auth-flow` 실행하여 인증 헤더/throw 분기 분석 완료 (Step 3-B)
* [ ] `analyze-auth-flow`의 `ddl_missing_tables`/`ddl_missing_columns`가 비어있음 (인터셉터 DB 의존성이 DDL에 모두 존재)
* [ ] `auth_db_dependencies`의 테이블 데이터가 setup SQL에 포함됨 (인증 통과용)
* [ ] `validate-test-code` 실행하여 정적 검증 통과 (Step 5, exit 0)

---

## 🚫 복수 API 엔드포인트 요청 시 파이프라인 생략 금지 (CRITICAL)

사용자가 여러 API 엔드포인트에 대해 한꺼번에 테스트 생성을 요청하더라도, **각 엔드포인트(핸들러 메서드)마다 반드시 전체 파이프라인(Step 0~6)을 개별 실행**해야 한다.

### 절대 금지 사항

* **test-plan 산출물 없이 테스트 코드를 생성하는 것은 금지한다.** 코드를 직접 읽고 "대충" 테스트를 만드는 것은 분기 경로 누락의 원인이 된다.
* 각 엔드포인트에 대해 최소한 Step 2(`generate-test-plan`)는 반드시 실행하여 `.nimbus/artifacts/test-plan/{ClassName}_{MethodName}.md` 산출물을 생성해야 한다.
* 산출물이 존재하지 않는 엔드포인트의 테스트 코드는 Step 4(Test Code Generation)로 진행할 수 없다.

### 컨텍스트 한계 도달 시 처리

한 세션에서 모든 엔드포인트를 처리할 수 없는 경우:

1. 현재 세션에서 완료한 엔드포인트 목록과 남은 엔드포인트 목록을 명시한다.
2. 다음 세션에서 남은 엔드포인트부터 이어서 전체 파이프라인(Step 0~6)을 실행한다.
3. **파이프라인을 생략하고 빠르게 끝내는 것은 절대 허용하지 않는다.**

### 검증 기준

* 모든 엔드포인트에 대해 `.nimbus/artifacts/test-plan/` 산출물이 존재해야 한다.
* 각 산출물의 체크리스트 항목과 테스트 메서드가 1:1 대응해야 한다.
* 산출물에 있는 케이스가 테스트 코드에서 누락되면 해당 테스트는 불합격이다.

Follow these instructions exactly whenever asked to generate integration tests for this project.


