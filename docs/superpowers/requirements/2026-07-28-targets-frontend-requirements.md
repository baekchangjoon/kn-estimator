# targets 범용 앞단 요구사항명세
> 출처(design spec): docs/superpowers/specs/2026-07-28-targets-frontend-design.md
> 완료 정의(DoD): 커버리지 대상 요구사항이 모두 ≥1개의 통과 수용 테스트를 가짐 (대상 매트릭스 전부 green)
> 리뷰: 3-벤더(Claude sonnet ×2 + Gemini; Cursor 슬롯 키체인 잠금 → Sonnet 폴백) 24건 전 건 수용 반영 (2026-07-28)

## 요구사항 목록

### REQ-001 — 텍스트 대상 목록 수용
- 유형: Functional
- 우선순위: Must
- 설명: `--targets <파일>`로 한 줄에 하나씩 적힌 대상 목록을 받아 N을 구성한다. 각 줄은 `\r\n` 제거 + 앞뒤 공백 strip 후의 문자열이 id다. 빈 줄과 `#` 시작 줄은 무시한다.
- 수용기준:
  - Given 3개 대상과 빈 줄·주석이 섞인 텍스트 파일, When `kn-estimate --targets list.txt --label template --model sonnet` 실행, Then exit 0이고 stdout에 `N=3`이 출력되며 kn-report.md/kn-plan.json이 생성된다.
- 검증 레벨: E2E black-box (CLI main() + tmp_path/capsys — 기존 테스트 관용)

### REQ-002 — stdin 목록 수용
- 유형: Functional
- 우선순위: Must
- 설명: `--targets -`는 stdin에서 **텍스트 목록**을 읽는다 (JSON은 `.json` 확장자 판별이라 stdin 불가 — 조용한 오파싱을 막기 위해 stdin 첫 비공백 문자가 `[`/`{`면 한국어 SystemExit).
- 수용기준:
  - Given stdin으로 파이프된 3줄 목록, When `--targets -`로 실행, Then REQ-001과 동일하게 동작하고 리포트 `대상:` 줄에 `(stdin, 3건)`이 표기된다.
  - Given stdin으로 파이프된 JSON 배열, When `--targets -`로 실행, Then "텍스트 목록 전용"을 밝힌 한국어 SystemExit.
- 검증 레벨: E2E black-box

### REQ-003 — 개수만 입력 (--n)
- 유형: Functional
- 우선순위: Must
- 설명: `--n <개수>`는 목록 없이 균일 w의 합성 대상 N개를 구성한다. 합성 id는 `len(str(N))` 자리 동적 제로패딩(`unit-001`…`unit-100`)이다. `--n`의 타입 검증(비정수 입력)은 argparse `type=int`의 표준 에러를 사전 검증으로 허용한다 — 한국어 SystemExit 요구는 파싱 통과 후의 값 검증(비양수)에만 적용한다.
- 수용기준:
  - Given 캘리브레이션 가용 셀, When `kn-estimate --n 100 --label template --model sonnet` 실행, Then exit 0이고 stdout에 `N=100`과 청크 수·k_avg·비용이 출력되며 plan.json의 id가 `unit-001`…`unit-100`이다.
- 검증 레벨: E2E black-box

### REQ-004 — 파일 경로 목록의 w·group 자동 추정
- 유형: Functional
- 우선순위: Must
- 설명: 텍스트 목록의 모든 줄이 실존 일반 파일(`is_file()`)이면 w=max(1, 파일크기/4) 토큰(0바이트 클램프), group=입력 표기 기준 부모 디렉터리(`str(Path(id).parent)` — cwd 상대화를 하지 않아 cwd 밖 절대 경로에서도 ValueError 없음)를 자동 사용한다. 자동 추정 group은 리포트 "그룹 단위" 섹션에서 명시 group과 동일하게 취급된다. is_file() 통과 후 크기 조회가 OSError를 내면(TOCTOU) 크래시 대신 w=1로 폴백한다. 공백 포함 경로도 한 줄=한 id라 정상 동작한다.
- 수용기준:
  - Given 크기가 뚜렷이 다른 실존 파일 3개(공백 포함 이름 1개, 0바이트 1개 포함)의 목록, When 실행, Then exit 0, 리포트의 w 상위 표 최상단이 가장 큰 파일이고, 같은 디렉터리 파일이 리포트 "그룹 단위" 섹션의 같은 행으로 집계되며, 0바이트 파일도 크래시 없이 w≥1로 포함된다.
  - Given cwd 밖 절대 경로의 실존 파일이 섞인 목록, When 실행, Then ValueError 없이 exit 0이고 해당 파일의 group이 그 절대 경로의 부모 디렉터리다.
- 검증 레벨: E2E black-box

### REQ-005 — 부분 비(非)일반-파일 시 균일 폴백과 경고
- 유형: Functional
- 우선순위: Must
- 설명: 목록 일부만 일반 파일이면 전체를 균일 w·무그룹으로 폴백하고 stderr에 건수·예시를 경고한다 (혼합 측정 금지). 경고 문구는 미실존과 디렉터리를 구분한다. 리포트에는 "균일 가정" 앵커 문자열을 포함한 한 줄이 실린다.
- 수용기준:
  - Given 실존 파일 2건 + 미실존 1건 + 디렉터리 1건 목록, When 실행, Then exit 0, stderr 경고에 미실존/디렉터리가 구분 집계되고 kn-report.md에 "균일 가정" 문자열이 있으며 그룹 단위 섹션이 없다.
- 검증 레벨: E2E black-box

### REQ-006 — JSON 정밀 목록
- 유형: Functional
- 우선순위: Must
- 설명: `.json` 확장자 목록은 `[{"id", "w"?, "group"?}]`로 파싱한다. id 필수(비어있지 않은 문자열)·중복 금지(리터럴 비교), w는 양수 숫자(생략 시 1.0), group은 문자열(생략 시 무그룹). id/w/group 외 키는 무시한다(관용 수용). 스키마 위반은 항목 인덱스를 병기한 한국어 SystemExit, JSON 구문 오류는 `load_calibration`의 JSONDecodeError 처리와 동일하게 한국어 SystemExit이다 (raw traceback 금지).
- 수용기준:
  - Given w/group과 여분 키(예: note)가 명시된 JSON 목록, When 실행, Then 명시된 w가 상대 크기로 반영되고 group이 묶음에 반영되며 여분 키로 실패하지 않는다.
  - Given id가 빠진(또는 비문자열 id인) 항목이 있는 JSON, When 실행, Then 해당 인덱스를 담은 한국어 SystemExit.
  - Given w가 0/음수/비숫자인 항목이 있는 JSON, When 실행, Then 해당 인덱스를 담은 한국어 SystemExit.
  - Given 최상위가 배열이 아닌 JSON, When 실행, Then 한국어 SystemExit.
  - Given 구문이 깨진 JSON 파일, When 실행, Then 한국어 SystemExit (traceback 미노출).
  - Given 같은 id가 두 번 적힌 JSON 목록, When 실행, Then 중복을 명시한 SystemExit (리터럴 비교 — 텍스트 경로의 resolve() 정규화는 JSON에 적용하지 않는다).
- 검증 레벨: E2E black-box

### REQ-007 — group 힌트의 묶음 반영과 --groups 출력
- 유형: Functional
- 우선순위: Must
- 설명: group이 같은 대상은 같은 청크에 우선 배치된다(무그룹 대상은 항목별 고유 합성 키로 자유 배치). "group 경계 존중"은 FFD의 최선-노력 힌트이므로, 검증은 **모든 group이 용량(cap) 이내로 들어가는 소규모 fixture**에서 "각 group의 항목 전부가 동일 청크 index에 있음"을 assert하는 것으로 정의한다. `--groups` 출력·리포트·plan.json은 대상 id로 표기하며, `--groups` 헤더는 `project_root` 없이 소스 라벨을 쓴다.
- 수용기준:
  - Given 2개 group으로 나뉜 소규모(용량 이내) JSON 목록과 `--groups`, When 실행, Then 각 group의 항목 전부가 같은 청크에 있고 항목이 id 문자열로 표기되며 헤더에 소스 라벨이 나타난다 (TypeError 없음).
  - Given 일부 항목만 group이 명시된 JSON 목록, When 실행, Then 리포트 "그룹 단위" 섹션에 명시 group 행만 나타나고 무그룹 항목의 합성 고유 키는 어떤 산출물에도 노출되지 않는다.
- 검증 레벨: E2E black-box

### REQ-008 — 소스 상호 배타
- 유형: Functional
- 우선순위: Must
- 설명: `project_root`·`--targets`·`--n` 중 정확히 하나만 허용한다. 0개 또는 2개 이상이면 한국어 SystemExit.
- 수용기준:
  - Given `project_root`와 `--targets` 동시 지정, When 실행, Then 한국어 SystemExit.
  - Given 세 소스 모두 미지정, When 실행, Then 한국어 SystemExit.
- 검증 레벨: E2E black-box

### REQ-009 — 목록·개수 입력 검증
- 유형: Functional
- 우선순위: Must
- 설명: 빈 목록·중복 id·`--n` 비양수·목록 파일 부재는 각각 원인을 밝힌 한국어 SystemExit이다. 중복 판정: strip된 id가 **실존 일반 파일이면** `resolve()` 정규화 후 비교(`./a`↔`a` 탐지), 그 외(미실존 경로·이름 문자열)는 리터럴 비교다 — 비경로 문자열의 표기 변형 중복은 감지 불가(단위 일관성 계약과 같은 사용자 책임, CONCEPTS.md에 명시).
- 수용기준:
  - Given 같은 실존 파일을 `./a.txt`와 `a.txt`로 적은 목록, When 실행, Then 중복을 명시한 SystemExit.
  - Given 유효 항목이 0건인(빈/주석뿐) 목록, When 실행, Then SystemExit.
  - Given `--n 0`, When 실행, Then SystemExit.
  - Given 부재 경로의 `--targets`, When 실행, Then SystemExit.
- 검증 레벨: E2E black-box

### REQ-010 — 공통 이상치 감지
- 유형: Functional
- 우선순위: Must
- 설명: 모든 앞단에서 w > 4×median(w)인 대상을 감지해(N≥4, median>0일 때) stdout과 리포트에 목록(최대 5건, 배수 병기)과 "별도 라벨로 분리 측정" 권고를 출력한다. stdout 위치는 `N=…` 요약 직후·`--groups` 블록과 파일럿 고지(ℹ)보다 앞이다. 권고문에 '파일럿' 단어를 쓰지 않으며, 강제 단독 배치는 하지 않는다. 균일 w 경로에서는 정의상 발동하지 않는다. 기존 순서 계약 테스트(`test_pilot_notice_follows_groups_block`)는 이상치 경고 활성 시나리오에서도 green이어야 한다.
- 수용기준:
  - Given 1건이 median의 4배를 넘는 JSON 목록과 `--groups`(캘리브레이션 미지정), When 실행, Then stdout에서 이상치 경고가 `N=` 줄보다 뒤·`그룹1(` 출력과 `ℹ`보다 앞에 나타나고, kn-report.md에도 경고·권고문이 있으며, stdout 어디에도 '파일럿'이 없다(파일럿 고지 ℹ 본문 제외 — 검증은 이상치 경고 블록에 한정).
  - Given 균일 w 목록, When 실행, Then 이상치 경고가 없다.
- 검증 레벨: E2E black-box

### REQ-011 — 스캐너 경로 회귀 불변
- 유형: Non-functional
- 우선순위: Must
- 설명: Spring 스캐너 앞단의 수치 결과(N·청크·비용)는 변하지 않는다. 리포트 골든은 이상치 라인 반영 시에만 재생성하고 HANDOFF.md를 갱신한다. 수용 테스트는 **신규**다 — 기존 근접 커버는 `test_improvements.py::test_build_plan_still_works_at_realistic_walls`(plan 모듈 단위, n_chunks==3만 검증)로 CLI 블랙박스 레벨이 아니며 중복이 아니다. 신규 테스트는 `_require_sut()` skip 관행을 따른다. **green 판정은 SUT(petclinic 클론) 보유 로컬 환경 기준이며, CI에서는 skip이 허용된다** (tests.yml의 기존 관행 — SUT 의존 테스트는 CI에서 skip).
- 수용기준:
  - Given 로컬 petclinic SUT, When 문서화된 기준 명령(CLI main())을 실행, Then stdout이 `N=18 chunks=3 k_avg=6.0 est=$21.18`이다.
- 검증 레벨: E2E black-box (실측 저장소; SUT 부재 시 skip)

### REQ-012 — 범용 소스의 리포트·고지 어휘 (design §7 전수표)
- 유형: Functional
- 우선순위: Must
- 설명: `--targets`/`--n` 실행의 출력은 design spec §7의 리터럴 전수표 **전체**를 따른다. `--out-dir`는 cwd 기준이다.
- 수용기준 (§7 표의 행별 대응 — 대표 샘플이 아니라 전 행 커버):
  - Given 무그룹 이름 문자열 목록의 `--targets` 실행, When kn-report.md 확인, Then `N = <n> 대상` 표기가 있고 "엔드포인트"·"미해결"·"그룹 단위"·"정적 슬라이스" 문구가 없으며 w 상위 표 대신 "균일 가정" 한 줄이 있고 산출물이 cwd 기준 out-dir에 생성된다.
  - Given `--n 5` 실행, When kn-report.md 확인, Then `(--n 5)` 소스 표기가 있다.
  - Given 파일 자동 w의 `--targets` 실행, When kn-report.md 확인, Then `## w 상위 10 대상` 헤더가 있고 표에 `external`/`unresolved` 열이 없으며 한계 고지가 "파일 크기(bytes/4)" 문구다.
  - Given JSON 명시 w의 `--targets` 실행, When kn-report.md 확인, Then 한계 고지가 "사용자 제공값" 문구다.
  - Given 캘리브레이션 미지정의 `--n` 실행(파일럿 고지 유발), When stdout 확인, Then 고지에 "EP 1개짜리"가 아닌 "대상 1개짜리"가 나타난다.
  - Given S0+delta_env가 W_soft의 90%를 넘는 캘리브레이션 fixture의 `--n` 실행(env-wall 경고 유발), When stdout 확인, Then "EP당"이 아닌 "대상당 1청크로 퇴화" 문구가 나타난다.
- 검증 레벨: E2E black-box

### REQ-013 — 초급자 개념 문서와 문서 동기화
- 유형: Functional (문서)
- 우선순위: Must
- 설명: docs/CONCEPTS.md(초급 엔지니어 대상: 앞단/뒷단, 스캐너, w, 캘리브레이션·라벨, 이상치(균일 w 불발 포함), 단위 일관성 계약, 단위별 요리책 — 예시 동반)를 신설하고 README(한/영)·GUIDE에서 연결한다. GUIDE.md 옵션 레퍼런스에 `--targets`/`--n`을 추가하고, CALIBRATION.md §6을 구현 완료로 갱신(제목의 "로드맵, 미구현" 제거, `--units` 어휘 제거)하며, HANDOFF.md에 새 골든·기능 한 줄을 반영한다.
- 수용기준:
  - Given 저장소, When 문서 검사 테스트 실행, Then docs/CONCEPTS.md가 존재하고 README.md·README.en.md가 이를 링크하며, GUIDE.md에 `--targets` 문자열이 있고, CALIBRATION.md에 `--units` 잔존 어휘와 "미구현" §6 제목이 없다.
  - HANDOFF.md 골든·기준선 갱신은 PR 문서 게이트에서 대조한다 (테스트 자동화 대상 아님 — 골든 해시가 구현 시점에 결정되므로).
- 검증 레벨: integration (문서 검사 테스트) + PR 문서 게이트

## 추적 매트릭스

| REQ-ID | 요구사항 | 수용 테스트 (tests/test_targets_frontend.py, 별도 표기 제외) | Level | Status |
|--------|----------|-------------|-------|--------|
| REQ-001 | 텍스트 목록 수용 | test_req001_text_list | E2E | 🟢 green |
| REQ-002 | stdin 목록 | test_req002_stdin, test_req002_stdin_json_rejected | E2E | 🟢 green |
| REQ-003 | --n 개수만 | test_req003_n_only | E2E | 🟢 green |
| REQ-004 | 파일 w·group 자동 | test_req004_auto_w_group, test_req004_absolute_path_outside_cwd | E2E | 🟢 green |
| REQ-005 | 부분 폴백·경고 | test_req005_partial_fallback | E2E | 🟢 green |
| REQ-006 | JSON 정밀 목록 | test_req006_json_list, test_req006_json_missing_id, test_req006_json_bad_w, test_req006_json_not_array, test_req006_json_syntax_error, test_req006_json_duplicate_id | E2E | 🟢 green |
| REQ-007 | group 묶음·--groups | test_req007_groups_output, test_req007_mixed_group_section | E2E | 🟢 green |
| REQ-008 | 소스 상호 배타 | test_req008_both_sources, test_req008_no_source | E2E | 🟢 green |
| REQ-009 | 입력 검증 | test_req009_duplicate_path_normalized, test_req009_empty_list, test_req009_n_zero, test_req009_missing_file | E2E | 🟢 green |
| REQ-010 | 이상치 감지 | test_req010_outlier_warning_position, test_req010_no_warning_uniform | E2E | 🟢 green |
| REQ-011 | 스캐너 회귀 불변 | test_req011_scanner_baseline_unchanged (test_targets_frontend.py에 신설 — test_kn.py 자체 러너는 픽스처 함수에 TypeError라 배치 불가; KN_SUT 존중, SUT 부재 시 skip — CI 허용) | E2E | 🟢 green (2026-07-29 SUT 재확보 실측 — 수치 요약·골든 해시 완전 일치, 이상치 미발동) |
| REQ-012 | 리포트·고지 어휘 | test_req012_report_vocabulary, test_req012_n_source_line, test_req012_file_w_report, test_req012_json_w_report, test_req012_pilot_notice_noun, test_req012_env_wall_noun | E2E | 🟢 green |
| REQ-013 | 개념 문서·동기화 | test_req013_concepts_doc_linked | integration | 🟢 green |

Coverage: 13/13 green (100%) — 대상: Must 13건. REQ-011은 2026-07-29 baekchangjoon/spring-petclinic 클론 재확보 후 실측으로 green 전환 (골든 불변 확인)
