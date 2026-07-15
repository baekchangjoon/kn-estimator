# kn-estimator 설계 — 테스트 생성 착수 전 K/N 사전 예측 도구

> 목적: 대상 Spring 프로젝트의 코드·설정만 보고(생성 실행 없이, LLM 호출 없이)
> **N(대상 엔드포인트 수), 엔드포인트별 작업량, 최적 청크 크기 K\*, 예상 비용/시간**을
> 계산한다. 프로젝트마다 다른 이 값들을 테스트 생성 착수 전에 산출해 실행 계획
> (청크 플랜)을 세우는 것이 목표다.
>
> 근거: `docs/scaling-analysis.md`의 비용 모델 + 28+13 run 실측 캘리브레이션.

## 1. 요구사항

- **입력**: 프로젝트 루트 (Maven/Gradle Spring, Java). 선택: 모델(opus/sonnet/haiku),
  모드(flat / flat+template), 컨텍스트 상한, 캘리브레이션 파일.
- **출력**: `kn-report.md`(사람용 보고서) + `kn-plan.json`(기계용 청크 플랜).
- **비 LLM·정적**: 코드 실행/빌드 없이 파일 스캔만으로 동작 (CI에서 수 초 내).
- **캘리브레이션 분리**: 비용 계수는 하드코딩하지 않고 데이터 파일로 분리, 실측
  원장(run_ledger.jsonl)에서 재계산 가능해야 한다.

## 2. 산출 항목과 계산 방법

### 2.1 N — 엔드포인트 인벤토리

기존 `harness/endpoints.py` 스캐너를 모듈로 재사용 (경로 조인 버그 수정판).
JSON 응답 핸들러만 채택(@RestController | JSON_VIEW | @ResponseBody). 산출:
엔드포인트 목록(method, path, controller, handler) + N.

### 2.2 엔드포인트별 작업량 w_i — 정적 슬라이스 토큰 추정

엔드포인트 i를 처리할 때 LLM이 읽게 될 코드량을 정적으로 근사한다:

```
w_i = tokens(controller_file)                       # 항상 읽음
    + Σ tokens(직접 참조 service/mapper 파일)        # 1-hop: 컨트롤러가 주입받는 필드 타입
    + Σ tokens(2-hop mapper XML / entity)           # service가 참조하는 매퍼·엔티티 (감쇠 0.5)
    + auth_overhead                                  # 인터셉터/시큐리티 설정 읽기 (프로젝트당 1회 상각)
    + external_overhead (외부 호출 시그니처 발견 시)  # RestTemplate/WebClient/Feign → 스텁 분석분
tokens(file) ≈ bytes/4  (한글 주석 비중이 높으면 /3 — 옵션)
```

1-hop 해석: 컨트롤러의 `@Autowired`/생성자 주입 필드 타입 → 같은 소스 트리에서 파일명
매칭. 2-hop: 동일 방식 1단계 추가 (그 이상은 감쇠 계수로 근사, 순환은 방문 집합으로 차단).

### 2.3 비용 모델 — 세션 시뮬레이션

scaling-analysis의 2차 모델을 "합으로 직접 계산"하는 이산 시뮬레이션으로 대체한다
(닫힌식보다 청크 플랜에 정확):

```
C0 = S0 + Σ_i∈chunk tokens(endpoint_table_row)      # 세션 시작 컨텍스트
처리 순서대로 EP i에 대해:
    reads_i  = τ · (C_현재 + w_i/2)                  # 이 EP 처리 중 턴들이 읽는 평균 컨텍스트
    C_현재  += δ_fixed + ρ·w_i                       # 도구 결과로 컨텍스트에 남는 몫
    out_i    = out_base + out_per_test · tests_i
cost = P_in·Σw_i(신규 read) + P_cache_read·Σreads_i + P_cache_write·(C_max) + P_out·Σout_i
```

캘리브레이션 파라미터 (모드×모델별, 실측 13 run에서 fit — 기본값 동봉):

| 파라미터 | 의미 | flat×opus 실측 | template×opus 실측 |
|---|---|---|---|
| S0 | 시작 컨텍스트 | 52K | 23K |
| τ | EP당 턴 수 | 6.2~10.4 (중앙 8.8) | 5.5~6.2 (중앙 5.9) |
| δ | EP당 컨텍스트 잔류 증가 | 16~26K (중앙 24K) | 15~23K (중앙 17.5K) |
| out/EP | EP당 출력 토큰 | 8~14K (중앙 12.7K) | 7.5~7.9K (중앙 7.6K) |
| (sonnet 배수) | τ 배수 | ×2.4 | ×1.05 |

w_i는 δ의 **프로젝트 간 이전을 위한 스케일러**로 쓴다: 캘리브레이션 프로젝트
(SmartPlant)의 평균 w̄_cal 대비 대상 프로젝트의 w_i 비율로 δ·out을 보정한다.
(`δ_i = δ_cal · (w_i / w̄_cal)^α`, α 기본 0.5 — 코드가 2배 커도 읽기가 2배 늘진 않음)

### 2.4 K\* — 최적 청크 크기와 청크 플랜

두 제약의 최소값:

1. **컨텍스트 벽**: `C0 + Σ_{i∈chunk} δ_i ≤ W_eff`. W_eff 기본 150K
   (Claude Code 자동 컴팩션 트리거) — 실측에서 376K까지 관측됐으나 컴팩션·품질
   리스크를 피하는 보수 기본값. `--context-limit`으로 조정.
2. **비용 최적**: K=1..K_max 그리드로 `⌈N/K⌉ 청크 분할 시 총비용`을 시뮬레이션해
   최소점 선택. (고정비 상각 ∝1/K vs 2차 항 ∝K의 트레이드오프)

청크 배정은 **컨트롤러 친화 우선** (같은 컨트롤러 EP를 같은 청크에 — 분석 컨텍스트
재사용) 후 w_i 균형(greedy bin-packing). 산출: `kn-plan.json`
`{"K": 10, "chunks": [{"endpoints": [...], "est_cost": 8.1, "est_context_peak": 141000}, ...]}`

### 2.5 보고서

`kn-report.md`: N, 복잡도 분포(w_i 히스토그램·상위 10), 모드×모델 예상 비용 매트릭스
(flat/template × opus/sonnet/haiku), 권장 구성(기본: template×sonnet), K\*, 청크 수,
예상 총비용·벽시계, 신뢰구간(캘리브레이션 분산 반영 ±폭), 전제·한계 고지.

## 3. CLI

```bash
python3 kn_estimator/estimate.py <project_root> \
  [--mode template|flat] [--model sonnet|opus|haiku] \
  [--context-limit 150000] [--calibration calibration.json] \
  [--out-dir .kn] [--json]
python3 kn_estimator/calibrate.py <run_ledger.jsonl> <transcripts_dir> > calibration.json
```

## 4. 검증 계획 (수용 기준)

1. **후방 검증(hold-in)**: SmartPlant에 대해 예측 vs 실측 —
   flat×opus N=8 예측이 $10.21의 **±40% 이내**, template×sonnet이 $5.63의 ±40% 이내,
   N=1 flat이 $3.39의 ±50% 이내. (계수를 같은 데이터에서 fit하므로 관대한 오차는
   회귀 방지용 sanity 기준)
2. **모델 간 순서 보존**: 예측이 실측 순서(template<flat, flat에서 opus<sonnet)를 유지.
3. **단위 테스트**: 인벤토리 수(167), 1-hop 슬라이스가 알려진 컨트롤러에서 기대 파일
   집합을 찾는지, K가 컨텍스트 제약을 위반하지 않는지, 청크 합집합=전체 N.
4. **타 프로젝트 스모크**: nimbus 저장소의 샘플(또는 graph-rag samples/order-service)에
   스캐너가 크래시 없이 N·플랜을 내는지.

## 개정 (v2 — 3벤더 리뷰 반영, 2026-07-09)

> 트리아지: `docs/superpowers/reviews/2026-07-09-kn-estimator-review-triage.md`.
> 아래 개정이 §2~4의 원문과 충돌하면 **개정이 우선**한다.

### v2.1 비용 모델 단순화 (K1, K2, K10)

파라미터는 셀(모드×모델)별 **관측 가능량 4개 + 가격표**로 축소한다:

```
셀별 캘리브레이션: S0(시작 컨텍스트), τ̄(EP당 턴), δ̄(EP당 컨텍스트 잔류), out̄(EP당 출력)
공변량: ŵ_i = w_i / w̄_project  (프로젝트 내 상대 작업량)
       δ_i = δ̄·ŵ_i^α,  out_i = out̄·ŵ_i^α,  τ_i = τ̄·ŵ_i^α
       α: 캘리브레이션 트랜스크립트의 EP별 (δ, w) 로그선형 fit. fit 불가 시 0.5로 두고
          α∈{0, 0.5, 1} 민감도 3열을 보고서에 병기.
청크 시뮬레이션 (트랜스크립트 파서와 동일한 집계 정의):
    C = S0;  cache_read = 0;  cache_write = S0;  out = 0
    for i in chunk(순서대로):
        cache_read  += τ_i · C          # 턴들이 현재 컨텍스트를 재읽음
        cache_write += δ_i              # 새로 잔류하는 컨텍스트
        C += δ_i;  out += out_i
    cost = P_cr·cache_read + P_cw·cache_write + P_out·out     # (P_in 항 제거 — 이중계산 방지)
총비용 = C_env_prep + Σ_chunk cost(chunk)                      # 공유 환경분석 1회 (K8)
wall  ≈ Σ_chunk (Σturns · latencȳ + out/ratē)  (병렬 시 max(청크) + cache_write 할증)
```

tests_i는 폐기. w_i는 절대 토큰이 아니라 **분위수·상대 비율**로 보고한다(bytes/4는
fallback 고지). 미캘리브레이션 셀(예: haiku 완료 전)은 수치 대신
`insufficient_calibration`을 출력한다. 가격표·캐시 배수는 calibration.json에 버전 포함.

### v2.2 정적 슬라이스 규칙 (K3)

1. **컨트롤러 파일 토큰은 청크당 1회만** 가산. EP 단위에는 핸들러 메서드 span만.
2. 주입 해석: `@Autowired` 필드 + 생성자 파라미터 + **Lombok `private final` 필드** 모두.
3. 타입이 인터페이스면 동일 트리에서 `*Impl` 폴백 매칭.
4. MyBatis: mapper 인터페이스 발견 시 `src/main/resources/**` 에서 네임스페이스/파일명
   일치 XML을 조인해 가산.
5. 매칭 실패는 조용한 0 금지 — `unresolved` 플래그 + 프로젝트 중앙값 prior로 대체하고
   보고서에 미해결률을 표기.

### v2.3 청크 플랜 (K4, K5)

- 고정 K 그리드 폐기. **W_target 그리드**(청크당 목표 누적 δ)로 파티션을 생성:
  컨트롤러 단위 묶음 → δ̂ 내림차순 First-Fit-Decreasing → 시뮬레이션으로 파티션
  총비용 평가 → 최소 파티션 채택. 벽 위반 청크는 로컬 재분할.
- 벽은 이층: `W_hard`(모델 컨텍스트 상한 — 위반 불가), `W_soft`(품질 정책 기본 180K
  — 실측 flat N=8 종료 분포 p50 역산값. 150K는 보수 프리셋 `--conservative`).
- 보고서는 K 단일값이 아니라 `len(chunks)`·청크별 (EP 수, est_cost, est_peak)·
  `K*_cost`/`K*_wall`을 병기.

### v2.4 검증 (K6 — 수용 기준 교체)

1. **N hold-out**: flat×opus를 N=1 run들만으로 캘리브레이션 → N=8 예측이 실측 3회
   범위 [min, max] 안에 들어야 한다 (템플릿 셀도 동일).
2. **Leave-one-out 커버리지**: 각 셀 3 run 중 2개로 fit → 나머지 1개가 예측구간
   (α 민감도 폭) 안에 드는 비율 ≥ 2/3.
3. **Σreads 재구성**: 캘리브레이션 트랜스크립트의 실측 cache_read 총량 대비 시뮬레이션
   재구성 오차를 보고 (수용 기준 아님, 관측치).
4. 순서 보존: template<flat (opus·sonnet 각), flat에서 opus<sonnet.
5. 외부 프로젝트(graph-rag samples/order-service)는 **스캔·플랜 스모크만** (실측 비용
   검증은 후속 실험으로 명시). 절대 USD는 비보증 — 상대 비교가 주 용도임을 보고서 상단 고지.

## 5. 한계 (보고서에 자동 고지)

- 캘리브레이션이 단일 프로젝트(SmartPlant) 실측 기반 — 타 프로젝트 절대값은 ±수십%
  오차 가능, **상대 비교(모드·모델·K 선택)가 주 용도**.
- 정적 슬라이스는 리플렉션·동적 SQL·설정 기반 라우팅을 과소평가할 수 있다.
- LLM 행동 분산(동일 조건 3회에서 ±30% 관측)이 예측 하한 오차다 — 점 추정이 아니라
  구간으로 제시한다.
