# kn-estimator 독립 분리 설계 (spec)

작성: 2026-07-16 · 대상 저장소: `kn-estimator` · 브랜치: `feat/standalone-package`

## 1. 목적과 범위

`tools/kn_estimator/`를 `reduce-token` 실험 저장소와의 결합 없이 **단독 설치·실행 가능한
Python 패키지**로 만든다. 이 문서는 **분리(리팩토링)만** 다룬다 — 행위 보존이 성립 조건이다.

### 1.1 범위 밖 (별도 단계)

리뷰 트리아지의 잔여 개선(K3(1) 컨트롤러 토큰 미소비, K2 α fit, K7 out/rate 항, K8
`C_env_prep` 1회 가산 등)은 **산출 수치를 바꾸므로** 이 단계에 포함하지 않는다. 완료 정의인
"기준선 동일 수치 재현"과 동시에 성립할 수 없기 때문이다. 분리 커밋 이후 별도 커밋으로,
각 수치 변화의 근거와 함께 구현한다.

## 2. 현재 상태와 결합 지점

| # | 결합 | 위치 | 영향 |
|---|---|---|---|
| 1 | 테스트 하드코딩 절대경로 | `tests/test_kn.py:6` `/home/baek/temp/reduce-token` | 이 저장소에서 테스트 전량 실패 |
| 2 | harness 스캐너 import | `scan.py:15-16` `sys.path.insert(.../harness)` | 패키지 외부 파일 의존 |
| 3 | 저장소 루트 추정 | `calibrate.py:124` `parents[2]` | 디렉토리 구조 변경 시 파손 |
| 4 | `sys.path.insert` 모듈 로딩 | `estimate.py:15-17`, `tests/test_kn.py:3` | 설치 불가, import 순서 의존 |
| 5 | 런타임 `results/` 의존 | `estimate.py:19-20` | 23MB 트랜스크립트 없으면 동작 불가 |
| 6 | 죽은 외부 스모크 경로 | `tests/test_kn.py:135` 타 세션 스크래치패드 절대경로 | 항상 SKIP — 실효 커버리지 0 |
| 7 | sibling import | `plan.py:7` `import model` | `sys.path` 주입에만 의존 → 패키지화 시 `ModuleNotFoundError` |

결합 5는 HANDOFF에 명시되지 않았으나 "독립 도구"의 실질적 최대 장애물이다. 결합 6·7은
3벤더 설계 리뷰(I2/I4)가 실측으로 잡아낸 누락분이다.

## 2.5 0단계 — 결정성 수리 (선행 필수)

설계 리뷰(I1)가 **완료 정의 자체가 성립 불가**임을 지적했고, 실측으로 확정했다.

`_injected_types()`가 set을 반환(`scan.py:82-85`)하고 `visit()`이 깊이별 감쇠를 적용
(`scan.py:122` `decay = 1.0 if depth == 1 else 0.5`)하는 조합 때문에, 공유 의존 타입의
가중치가 **어느 부모가 먼저 방문하느냐**로 갈렸다. set 순회 순서는 Python 해시 랜덤화에
좌우되므로 실행마다 결과가 달라졌다:

- `sum_w` 5회 실측: `1827012 / 1825368 / 1828992 / 1832280 / 1830636` (폭 ~0.4%)
- `kn-plan.json` sha256: 6회 중 4종
- `flat/opus`: 44청크 `$246.42~$246.43` ↔ 45청크 `$248.91~$248.92` 진동
- `PYTHONHASHSEED=0` 고정 시 3/3 동일 → 원인 확정

즉 리팩토링을 올바르게 해도, 아예 하지 않아도 골든 비교가 무작위로 성공/실패한다.

**수리:** 순회를 **BFS + 정렬**로 교체해 감쇠를 *최단 깊이*의 함수로 만든다. 정렬만으로도
결정성은 얻지만 `w_i`가 클래스명 알파벳 순서라는 무의미한 우연에 계속 의존한다. DFS는 두
가지를 오염시킨다 — ①실제 깊이 1인 타입이 다른 경로로 깊이 2에서 먼저 닿으면 감쇠 0.5를
잘못 받고, ②`depth > 2` 컷오프 때문에 어느 하위 트리가 탐색되는지 자체가 순서로 갈린다.
BFS는 둘 다 제거한다. 따라서 이 수리는 재현성 확보이자 정확도 개선이다.

**행위 보존 판정:** 수리 후 값(`template/sonnet` `$143.91`, `flat/opus` 45청크 `$248.91`)은
수리 전 관측 대역 **안**이다(8회 실측으로 확인). 의미를 바꾼 것이 아니라 도구가 이미 내던
값 중 하나를 정준값으로 고정한 것이다. HANDOFF 기준선의 안정 지표(`N=167`, `chunks=60`,
`k_avg=2.8`)는 전부 보존된다.

### 2.1 harness 사용 실측

estimator가 `harness/endpoints.py`에서 실제로 쓰는 심볼은 **`scan`, `_methods` 둘뿐**이다
(`scan.py:41`, `scan.py:89`). `select_n8`·`N1`·`__main__`은 reduce-token 실험 전용(N=8 표본
선정·사전등록 산출물)이며 estimator 경로에서 호출되지 않는다 → vendoring 시 제외한다.

## 3. 목표 구조

```
pyproject.toml                 # setuptools, console_scripts: kn-estimate
src/kn_estimator/
  __init__.py
  endpoints.py                 # harness/endpoints.py에서 vendored (scan/_methods/헬퍼만)
  scan.py                      # from . import endpoints
  model.py  plan.py  calibrate.py
  cli.py                       # 구 estimate.py의 main()
  data/calibration.json        # 사전 산출 캘리브레이션 (패키지 데이터)
tests/test_kn.py
```

`harness/`는 vendoring 후 삭제한다. `plan.py:7`의 `import model`은 `from . import model`로,
`scan.py`의 harness import는 `from . import endpoints`로 바꾼다(결합 7).

### 3.1 캘리브레이션 데이터 전략 (핵심 결정)

현재는 실행마다 `results/run_ledger.jsonl` + `results/runs/*/transcript.jsonl`(23MB)을 읽어
캘리브레이션을 재계산한다. 이를 다음으로 분리한다:

- **런타임**: 패키지 동봉 `data/calibration.json`을 로드(수 KB). `--calibration <path>`로
  오버라이드 가능(기존 CLI 플래그 유지).
- **오프라인 재생성**: `calibrate.py`를 argparse CLI로 만들어 원장·runs 경로를 **인자로** 받아
  `calibration.json`을 산출한다(결합 3 해소). `results/`는 저장소에 남되 런타임 의존이 아니다.

행위 보존 근거: 동봉 JSON은 시딩 원장으로 생성한 **동일 dict의 직렬화**이며, Python의
float↔JSON 왕복은 `repr` 기반이라 값이 보존된다. 골든 파일 비교로 실증한다(§5).

### 3.2 테스트 경로 해석

`ROOT` 하드코딩을 제거하고 다음 순서로 해석한다:
1. 환경변수 오버라이드 — SUT는 `KN_SUT`, 원장은 `KN_LEDGER`/`KN_RUNS`
2. 저장소 상대 기본값 — 테스트 파일 자신 기준 `Path(__file__).resolve().parents[1]`의
   `smartplant/`, `results/` (디렉토리 이동 시 깊이도 함께 갱신)

SUT(`smartplant/`)는 gitignore 대상이므로 **부재 시 skip**한다(조용한 pass 금지 — skip 사유를
명시 출력). 원장 기반 테스트는 `results/`가 커밋돼 있으므로 항상 실행된다.

## 4. 데이터 흐름 (변경 없음)

```
project_root ──scan.inventory──> eps ──scan.build_slices──> slices ─┐
data/calibration.json ──load──> cal ──────────────────────────────┴─> plan.build_plan
                                                                      └─> model.simulate_chunk
                                                                          └─> kn-report.md / kn-plan.json
```

모듈 경계·함수 시그니처·산출 스키마는 보존한다. 변경은 **import 방식과 캘리브레이션 로드
지점**뿐이다.

## 5. 수용 기준 (E2E / 완료 정의)

아웃오브프로세스 블랙박스: 설치된 `kn-estimate` 콘솔 스크립트를 실제 실행해 산출물을
리팩토링 **이전에 고정한 골든 파일**과 비교한다.

| REQ | 기준 | 검증 |
|---|---|---|
| E2E-1 | 인벤토리 = 167 EP | `scan.inventory(SUT)` 길이 |
| E2E-2 | 캘리브레이션 5셀 (`flat/opus`, `flat/sonnet`, `template/opus`, `template/sonnet`, `template/haiku`), `flat/haiku` 부재 | 동봉 JSON `cells` 키 집합 |
| E2E-3 | `kn-estimate smartplant --mode template --model sonnet` → `N=167 chunks=61 k_avg=2.7 est=$145.05` (B단계 K3(3) 반영 후) | 파싱된 수치 비교 |
| E2E-4 | `kn-plan.json` sha256 = `fa5596936e81f776eeadde1e3cdd80832cc7433b88fdc76e6d24dc6c06843910` | 바이트 비교 |
| E2E-5 | `kn-report.md` sha256 = `12634bf3f92ec348b288a449f6fa7aa10e72d8587af17478caf42a643de51a44` | 바이트 비교 |
| E2E-6 | 기존 단위 테스트(hold-out, LOO 커버리지, 순서 보존, 파티션 불변식) 전량 green | pytest |
| E2E-7 | 해시 시드 5종에서 `sum_w` 동일 (결정성 회귀) | `tests/test_determinism.py` |

골든 파일은 **0단계(결정성 수리) 이후** 코드로 생성해 고정했다(3/3 재현 확인). 수리 전
골든(`ddcc4264…`)은 비결정 표본이었으므로 폐기했다. 이것이 리팩토링의 행위 보존 증거다 —
수치가 1비트라도 달라지면 실패다.

> **골든 생성 시 주의:** `smartplant/`는 워크트리 간 심볼릭 링크로 공유되므로 기본
> `--out-dir .kn`에 쓰면 다른 체크아웃의 실행이 덮어쓴다. 골든은 `--out-dir .kn-golden`처럼
> 격리된 경로로 생성한다(실제로 이 오염을 한 번 겪었다).

## 6. 에러 처리

- 동봉 `calibration.json` 부재/파손 → 명시적 예외(조용한 폴백 금지). 재생성 명령을 메시지에 포함.
- `KN_SUT` 미설정 + `smartplant/` 부재 → 해당 테스트 skip + 사유 출력.
- 미캘리브레이션 셀 → 기존대로 `insufficient_calibration`(동작 보존).

## 7. 알려진 위험

- **골든 파일이 dict 순회 순서에 의존할 여지**: Python 3.7+ dict는 삽입순 보장이고, 셀 생성
  순회는 `groups` 삽입순이며 원장 파일 순서가 고정이라 결정적이다. 다만 `glob` 결과 순서가
  파일시스템 의존일 수 있어(`_Index.by_class`, `xmls`) — 리팩토링이 이 순회를 건드리지
  않으므로 기준선 재현으로 검출된다.
- **패키지 데이터 누락**: `pyproject.toml`에 `package-data` 미설정 시 설치본에서
  `calibration.json`이 빠진다 → E2E를 **설치된 콘솔 스크립트**로 돌려 검출한다.

## 8. 비범위 명시

- 타 Spring 프로젝트 일반화, rv3 원장 어댑터, 리뷰 잔여 개선은 후속 단계.
- README의 "절대 USD 비보증 — 단일 프로젝트 캘리브레이션" 한계 고지는 그대로 유지한다.
