# kn-estimator 상세 가이드 — 동작 원리 · 동작 방식 · 사용 방법

> 대상 독자: LLM 테스트 생성 파이프라인(nimbus 등)을 새 프로젝트에 적용하기 전에
> "몇 개 엔드포인트(N)를, 몇 개씩 묶어서(K), 얼마의 비용/시간으로" 돌릴지 계획해야
> 하는 엔지니어. LLM 호출 없이 파일 스캔만으로 수 초 안에 답을 낸다.

---

## 1. 왜 필요한가 (문제 정의)

실측 43 run(SmartPlant, `results/run_ledger.jsonl`)에서 확인된 사실:

1. **단일 LLM 세션의 비용은 엔드포인트 수 N에 대해 2차(quadratic)로 증가한다.**
   세션이 엔드포인트를 처리할수록 도구 결과가 대화에 누적되고(엔드포인트당 +16~26K
   토큰), 매 턴의 읽기 비용은 그 누적 컨텍스트 전체에 비례하기 때문이다.
2. **컨텍스트 벽이 비용보다 먼저 온다.** N=8에서 이미 세션 종료 컨텍스트가
   178~376K — 컴팩션(캐시 무효화+지침 손실)과 지침 망각이 뒤따른다.
3. 따라서 큰 N은 **K개씩 묶은 독립 세션(청크드 flat)** 으로 돌려야 하고, 최적 K는
   "고정 오버헤드 상각(∝1/K)"과 "2차 항+품질 리스크(∝K)"의 트레이드오프로 정해진다.
4. 이 트레이드오프의 계수는 **프로젝트마다 다르다** (엔드포인트 수, 코드 복잡도,
   DI 구조, 매퍼 크기). → 착수 전에 프로젝트를 읽고 K·N·비용을 계산하는 도구가 필요.

## 2. 동작 원리 (모델)

### 2.1 비용 모델 — 관측 가능량 4개로 세션을 시뮬레이션

캘리브레이션 셀(생성 방식 × 모델, 예: `template/sonnet`)마다 실측 트랜스크립트에서
다음 4개 값을 추출한다 (전부 직접 관측 가능한 양 — 리뷰 반영으로 추정 파라미터 배제):

| 파라미터 | 의미 | 추출 방법 |
|---|---|---|
| **S0** | 세션 시작 컨텍스트 | 첫 assistant 턴의 (cache_read+input+cache_write) |
| **τ** | 턴 수 | message.id 기준 dedup한 assistant 턴 수 |
| **δ** | 컨텍스트 잔류 증가 | (세션 최대 컨텍스트 − S0) |
| **out** | 출력 토큰 | 원장의 output_tokens |

τ·δ·out은 **env(프로젝트당 1회 고정분)과 EP(엔드포인트당 한계분)으로 2점 분해**한다:
같은 셀에 N=1과 N=8 실측이 있으면 연립으로 풀고(`X(N) = X_env + X_ep·N`),
단일 N만 있는 셀은 flat/opus의 env:EP 비율을 차용한다(`env_split_approx` 플래그).

청크(엔드포인트 K개 묶음) 하나의 비용은 턴 단위로 직접 합산한다:

```
C = S0
cache_read  += τ_env·(C + δ_env/2);  C += δ_env          # 환경분석 (청크당 1회)
for i in chunk:                                            # 엔드포인트 순회
    cache_read  += τ_i·(C + δ_i/2)                        # 이 EP의 턴들이 현재 컨텍스트를 재읽음
    C += δ_i;  cache_write += δ_i;  out += out_i
cost = P_read·cache_read + P_write·cache_write + P_out·out
```

가격표(P_*)는 실측 캘리브레이션 완료값(Opus $5/$25, Sonnet $3/$15, Haiku $1/$5 MTok,
cache write 2.0x·read 0.1x — Claude Code 1h 캐시)이며 calibration.json에 버전과 함께
동봉된다. 이 식의 집계 정의는 실측 트랜스크립트 파서와 동일하다 — 즉 시뮬레이션
결과와 실측 원장이 같은 단위로 비교된다.

### 2.2 프로젝트 간 이전 — w 공변량

캘리브레이션은 특정 프로젝트(SmartPlant) 실측이므로, 대상 프로젝트의 엔드포인트가
더 무겁거나 가벼우면 보정이 필요하다. 엔드포인트 i의 정적 작업량 `w_i`(§3.2)를
프로젝트 평균으로 나눈 상대값 `ŵ_i`로 δ·out·τ를 곱셈 스케일한다:

```
δ_i = δ_ep · ŵ_i^α     (out, τ 동일)
```

α는 "코드가 2배 커지면 읽기가 몇 배 늘어나는가"의 탄성이다. 다중 프로젝트 실측이
쌓이기 전까지 기본 0.5로 두되, **α∈{0, 0.5, 1} 민감도가 예측구간(좁은 CI)으로
자동 병기**된다. 여기에 실측 run 간 분산(동일 조건 3회 ±30~46%)을 풀링한 곱셈
밴드(넓은 CI)를 결합해 `pi_low ~ pi_high`를 낸다. **점 추정이 아니라 구간으로
읽어야 한다.**

### 2.3 K 결정 — 이층 벽 + 파티션 최적화

- **W_hard** (기본 900K): 모델 컨텍스트 상한 — 어떤 청크도 초과 불가. 모델별
  윈도우의 90%로 자동 캡된다 (opus/sonnet 1M→900K, haiku 200K→180K) — `--w-hard`를
  더 크게 줘도 모델 상한을 넘길 수 없다. 보고서·플랜의 `w_hard`는 캡 적용 후 유효값.
- **W_soft** (기본 180K): 품질 정책 벽 — 실측에서 게이트 통과 세션의 종료 컨텍스트
  분포(p50)로 역산한 값. 초과 시 비용에 15% 패널티를 부과하고 보고서에 경고
  (컴팩션·후반부 품질 저하 리스크). `--conservative`로 150K 프리셋. 유효 W_hard보다
  크게 주면 유효 W_hard로 캡된다 (예: haiku에 `--w-soft 400000` → 180K로 재해석,
  보고서에 캡 사실 표기).
- 파티션 생성: 컨트롤러 단위로 묶고(같은 컨트롤러의 EP는 분석 컨텍스트를 공유하므로)
  → δ̂ 내림차순 First-Fit-Decreasing으로 용량(W_target) 빈에 배치 → W_target 그리드
  {0.4, 0.55, 0.7, 0.85, 1.0}×soft예산에서 **파티션 전체를 시뮬레이션해 총비용이
  최소인 분할을 채택**한다. 단일 컨트롤러가 용량을 넘으면 그 컨트롤러만 EP 단위 분할.
- 보고서의 진실원은 "K"라는 단일 숫자가 아니라 **`len(chunks)`와 청크별
  (EP 수, 예상 비용, 예상 피크 컨텍스트)** 다. K_avg는 요약 지표일 뿐이다.

### 2.4 신뢰성 장치

- **게이트 통과 run만 캘리브레이션에 사용** (실패 run은 조기 종료로 비용이 과소해
  계수를 오염시킴). 측정 인프라 위양성으로 재판정된 run은
  `results/gate-adjudications.json`에 명시된 것만 구제.
- 셀당 게이트 통과 run이 2개 미만이면 수치를 내지 않고 **`insufficient_calibration`**
  을 출력한다 (예: flat/haiku — 실측에서 완주 불가 판정).
- 검증(테스트 10종): N hold-out(부분 데이터 fit → 나머지 예측), leave-one-out
  예측구간 커버리지 11/12, 순서 보존(template<flat, flat에서 opus<sonnet),
  파티션 불변식(전 EP 커버·중복 없음·벽 준수), 외부 프로젝트 스모크.

## 3. 동작 방식 (파이프라인 4단계)

```
project_root
  │ ① scan.inventory()      — 엔드포인트 인벤토리 (N)
  │ ② scan.build_slices()   — 엔드포인트별 정적 슬라이스 (w_i)
  │ ③ calibrate.calibrate() — 실측 원장 → 셀별 계수 (또는 --calibration 파일)
  │ ④ plan.build_plan()     — 파티션 최적화 → 청크 플랜 + 비용
  ▼
.kn/kn-report.md (사람용) + .kn/kn-plan.json (기계용)
```

### ① 인벤토리

Spring 컨트롤러를 정규식 스캔해 JSON 응답 핸들러만 채택한다
(`@RestController` | `JSON_VIEW` 반환 | `@ResponseBody`). 클래스/메서드 레벨
`@RequestMapping`(및 `@Get/Post/...Mapping`)을 슬래시 규칙으로 조인한다.

### ② 정적 슬라이스 — LLM이 읽게 될 코드량의 근사

엔드포인트 i에 대해:

- **핸들러 메서드 span** 토큰 (컨트롤러 파일 전체가 아님 — 컨트롤러 본체는
  `controller_shared_tokens`로 분리되어 청크 단위에서 1회만 상각)
- **1-hop**: 컨트롤러가 주입받는 타입 — `@Autowired` 필드, 생성자 파라미터,
  **Lombok `private final` 필드** 모두 인식. 타입이 인터페이스면 동일 트리의
  `*Impl` 폴백.
- **2-hop**: 서비스가 주입받는 DAO/Mapper (감쇠 0.5). DAO/Mapper 발견 시
  `src/main/resources/**`의 **패키지 병치 MyBatis XML을 조인**해 SQL 크기까지 가산.
- `RestTemplate`/`WebClient` 발견 시 `external_call` 플래그 (스텁 작업량 신호).
- 매칭 실패는 조용한 0이 아니라 `unresolved` 플래그 + 프로젝트 중앙값 prior로 대체,
  보고서에 미해결률 표기.

토큰 환산은 bytes/4 근사(fallback)이며, w는 절대값이 아니라 **분위수·상대 비율**로
제시된다.

### ③ 캘리브레이션

기본값은 패키지에 동봉된 사전 산출 캘리브레이션(`data/calibration.json`)이다. 계수에 반영된 run은 18건이다 (flat/opus 6, flat/sonnet 3, template/opus 3, template/sonnet 3, template/haiku 3). 자체 실측이 쌓이면:

```bash
kn-calibrate --ledger results/run_ledger.jsonl --runs results/runs --out my-calibration.json
kn-estimate <root> --calibration my-calibration.json
```

`--calibration` 없이 실행하면(=동봉 SmartPlant 계수) CLI가 그 사실과 §4.4 파일럿
절차를 자동 고지한다 — 캘리브레이션은 실측 원장이 필요해 도구가 대신 수행할 수 없다.

원장에 등장하지만 산출에서 빠진 셀은 `skipped_cells`에 사유와 함께 기록되고 stderr로
경고된다 — `no_usable_runs(gate_fail=…, missing_transcript=…)`(게이트 전멸·트랜스크립트
전멸), `insufficient_runs(...)`(표본<2, 트랜스크립트 부재분 병기), 또는
`single_n_without_reference_cell(flat/opus)`(단일 N 셀인데 env:ep 분해 기준이 될
flat/opus 2점 fit이 원장에 없음). 원장에 variant 자체가 없는 셀은 기록하지 않는다.
`kn-estimate`의 매트릭스도 이 사유를 `insufficient_calibration (…)`으로 병기한다.

### ④ 플랜·보고서

- `kn-plan.json`: 청크별 엔드포인트 목록·예상 비용·피크 컨텍스트 — 러너가 그대로
  순회하며 청크당 1세션을 실행하면 된다 (청크드 flat 실행 계획).
- `kn-report.md`: N, w 분포(p33/p66/max), 권장 플랜, **모드×모델 전체 매트릭스**,
  복잡도 상위 10 엔드포인트, 한계 고지.

## 4. 사용 방법

### 4.1 기본 사용

```bash
kn-estimate /path/to/your-spring-project \
    --mode template --model sonnet
# → your-spring-project/.kn/kn-report.md, kn-plan.json
```

### 4.2 CLI 옵션 전체

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--mode` | template | 생성 방식: `template`(spec→렌더러) / `flat`(직접 저작) |
| `--model` | sonnet | opus / sonnet / haiku (미캘리브레이션 셀은 수치 미제공) |
| `--calibration` | 내장 실측 | calibration.json 경로 (자체 실측으로 교체 가능) |
| `--w-soft` | 180000 | 품질 정책 벽 (초과 시 패널티+경고, 유효 W_hard로 캡) |
| `--w-hard` | 900000 | 모델 상한 벽 (위반 불가, 모델별 윈도우×0.9로 자동 캡) |
| `--conservative` | off | W_soft=150K 보수 프리셋 |
| `--parallel` | off | 청크 병렬 실행 가정 (벽시계=max, cache_write 할증) |
| `--groups` | off | 비용 최적 생성 묶음을 "그룹N(EP, …) — $비용" 실행 지시로 출력 |
| `--out-dir` | .kn | 출력 디렉토리 |

### 4.3 결과 해석 가이드

1. **매트릭스에서 구성을 고른다** — 예상 총비용·벽시계로. **모드 우열은 프로젝트와 N의
   함수다**: SmartPlant형(대규모·중SQL 레거시)에서는 `template×sonnet`이 우세했으나,
   다중 프로젝트 실측(2026-07 캠페인)에서는 **소형 모던 서비스 3/3에서 flat×sonnet이
   더 쌌다** — template의 인프라 구축 고정비(out_env 3~4배)가 EP당 절감을 압도한다.
   자체 캘리브레이션이 있으면 도구로 모드 교차점 N을 직접 계산하라 (실측 예: auth-user
   N≈9부터 template 우세 — 단 그 서비스는 N=5라 flat이 정답). 비용 절대 최소는
   여전히 `template×haiku`(단, 검증 깊이 캐비앗 + 소형 프로젝트에서 셀 전멸 리스크 —
   캠페인에서 petclinic 0/6). 근거: `docs/2026-07-20-multi-project-calibration-campaign.md` §3.
2. **`n_chunks`·`k_avg`를 실행 계획으로 쓴다** — kn-plan.json의 청크 순서대로
   세션을 돌리고, 게이트 실패 청크만 재실행한다. 보고서의 `K*_cost`(셀 단가 최소 K)·
   `K*_wall`(W_soft 용량 상한 K)은 평균 w 기준 참고 지표다 — 실제 파티션은 컨트롤러
   경계를 우선하므로 k_avg가 이와 다를 수 있다. 보고서의 **비용 곡선**
   `C(K) ≈ a + b·K + c·K²`(a: 청크 고정비, b: EP 한계비용, c: 컨텍스트 누적 항)와
   **컨트롤러 단위** 표(n·Σw·배정 청크, kn-plan.json `cost_curve`/`controllers`)로
   구성 간 비교와 단위별 배치를 읽는다. 컨트롤러 단위의 a,b,c 분화는 제공하지
   않는다 — 근거: `2026-07-26-cost-curve-and-unit-coefficients.md`.
3. **비용은 구간으로 읽는다** — 실측 run 분산이 ±30~46%였다. `pi_low~pi_high` 밖의
   결과가 나오면 캘리브레이션 재생성을 검토한다.
4. **`unresolved` 비율이 높으면(>20%)** 정적 슬라이스가 그 프로젝트의 DI 패턴을
   못 읽는 것 — w 순위의 신뢰도가 낮아지므로 청크 플랜은 컨트롤러 묶음 위주로만
   참고한다.

### 4.4 새 프로젝트에 정확도를 높이는 절차 (권장 워크플로)

```
1) kn-estimate로 초기 플랜 산출 (내장 캘리브레이션, 절대값은 참고치)
2) 같은 모드×모델로 **크기가 다른** 그룹 2개 이상 실행 (파일럿 — 예: EP 1개짜리
   + 가장 작은 그룹). kn-calibrate의 env/ep 분해가 N 2점을 요구하고 셀당 run<2는
   산출에서 제외되므로, 그룹 1개나 같은 크기 2개로는 계수가 나오지 않는다.
   반복 2~3회면 분산 밴드까지 실측 기반이 된다 (±10% 수치는 N 2점×반복 3 설계).
3) 파일럿의 run_ledger/트랜스크립트로 kn-calibrate 재실행 → 프로젝트 자체 계수
4) 게이트 통과 세션의 컨텍스트 분포로 --w-soft 재산정 (p50~max 참고)
5) --calibration + --w-soft로 재추정 → 나머지 청크 실행
```

**파일럿 원장 스키마** — `kn-calibrate --ledger`가 읽는 `run_ledger.jsonl`은 run당
한 줄의 JSON이다 (필수 필드):

```json
{"run_id": "myproj_flat_template_sonnet-n3-r1", "variant": "flat_template_sonnet",
 "role": "run_total", "n": 3, "rep": 1, "gate": "pass",
 "cost_usd": 5.1, "output_tokens": 41000, "wall_s": 900}
```

- `variant` → 셀 매핑: `flat`=flat/opus, `flat_sonnet`, `flat_haiku`,
  `flat_template`=template/opus, `flat_template_sonnet`, `flat_template_haiku`.
- `--runs` 디렉토리에는 `<run_id>/transcript.jsonl`(Claude Code 세션 트랜스크립트)을
  두면 assistant 메시지 usage에서 S0·턴 수·최대 컨텍스트를 복원한다.
- `gate`가 `pass`인 run만 계수에 반영된다 (실패 run은 조기 종료로 비용이 과소).

이 파일럿-재캘리브레이션 루프가 "단일 프로젝트 캘리브레이션의 이전 한계"(3벤더 리뷰
공통 지적)에 대한 실무 대응이다. **다중 프로젝트 캠페인 실측(2026-07)으로 검증됨**:
내장 캘리브레이션의 오차 −23~−34%가 자체 캘리브레이션+W_soft 재산정 후 **±10% 이내**로
줄었다 (auth-user 예측 $6.66 vs 실측 $7.13, community $5.81 vs $6.17).

4)를 생략하면 안 되는 이유: 현행 모델 세대의 환경 고정분(S0+δ_env)이 기본 W_soft(180K)를
사실상 채워 **파티션이 EP당 1청크로 퇴화하고 비용이 3~6배 과대추정**된다 — 이때 CLI가
경고를 낸다. 실측 상 EP당 한계 계수(δ_ep·out_ep)는 프로젝트 간 ±25% 내로 이전되지만
환경 고정분(τ_env·out_env)은 3~4배까지 벌어지므로, 파일럿이 갱신하는 것은 주로 env다.
상세: `docs/2026-07-20-multi-project-calibration-campaign.md`.

### 4.5 한계 (요약)

- **절대 USD 비보증** — 주 용도는 모드·모델·K의 상대 비교와 실행 플랜.
  (2026-07 캠페인 실측: 내장 캘리브레이션의 타 프로젝트 오차 −23~−34%,
  파일럿 재캘리브레이션 후 ±10% — §4.4.)
- 정적 슬라이스는 리플렉션·동적 라우팅·설정 기반 빈을 과소평가할 수 있다.
- 캘리브레이션은 N≤8 관측 기반 — 대규모 N 외삽은 N=16/32 스윕 전까지 미검증.
- Java/Spring + Maven/Gradle **단일 모듈** 구조 가정 — 멀티모듈은 모듈 디렉토리를
  root로 개별 실행. 어노테이션 기반 MVC/WebFlux만 인식 (함수형 라우팅 비가시),
  JPA 데이터 계층은 리포지토리 인터페이스+엔티티 1-hop까지만 반영. 다른 스택은
  스캐너 교체 필요 (`scan.py`의 inventory/슬라이스 규칙만 바꾸면 모델·플랜은 재사용 가능).
- 참조용 타 프로젝트 캘리브레이션 3종(petclinic·auth-user·community)이
  `results/campaign/analysis/`에 있다 — `--calibration`으로 바로 사용 가능.

## 5. 파일 구성

| 파일 | 역할 |
|---|---|
| `cli.py` | CLI 엔트리 (`kn-estimate`) — 스캔→캘리브레이션→플랜→보고서 |
| `scan.py` | 인벤토리 + 정적 슬라이스 (Spring 관용구 해석) |
| `calibrate.py` | 실측 원장·트랜스크립트 → 셀별 계수 (env/EP 2점 분해) |
| `model.py` | 청크 시뮬레이션 + 예측구간 (α 민감도 × run 분산 밴드) |
| `plan.py` | 컨트롤러 친화 FFD 파티션 + W_target 그리드 최적화 |
| `tests/test_kn.py` | 검증 10종 (hold-out, LOO 커버리지, 불변식, 스모크) |

설계 근거와 3벤더 리뷰 반영 내역: `docs/2026-07-09-kn-estimator-design.md`,
`docs/2026-07-09-kn-estimator-review-triage.md`.
