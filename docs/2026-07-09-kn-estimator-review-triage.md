# kn-estimator 설계 3벤더 리뷰 트리아지 (Gemini 3.1 Pro / Sonnet / GPT(auto))

> 원문: scratchpad kn-review-{gemini,sonnet,gpt}.txt. 세 리뷰의 지적이 강하게 수렴 —
> 전부 실질 결함으로 판단, 아래와 같이 반영한다. 설계 v2는 스펙 문서의 "개정(v2)" 절.

## 반영 (설계 v2에 적용)

| # | 출처 | 지적 | 조치 |
|---|------|------|------|
| K1 | S#1, G#1(gpt) | 비용 식 자기모순 (δ_fixed+ρ·w_i vs δ_cal·ŵ^α 병존, tests_i 산출 경로 없음, P_cache_write·C_max 과금 정의 오류, 입력 이중계산) | **관측 가능량만으로 축소**: 셀별 (S0, τ, δ̄, out̄) + 가격표. w_i는 δ·out의 곱셈 공변량(ŵ^α)으로만. tests_i 폐기. 집계 정의를 트랜스크립트 파서와 동일하게 고정 |
| K2 | S#3, G#2(gpt), Gem#2 | α=0.5 근거 없음, τ 상수 가정 | α는 캘리브레이션 데이터의 EP별 로그선형 fit으로 추정하되 실패 시 [0,1] 민감도 구간을 보고서에 병기. τ도 동일 공변량으로 스케일 |
| K3 | Gem#1·#6, S#4, G#3(gpt) | 정적 슬라이스가 Spring 관용구를 놓침 (인터페이스 주입, Lombok, MyBatis XML) + 컨트롤러 파일을 EP마다 중복 가산 | (1) 인터페이스→`*Impl` 폴백, (2) Lombok `private final` 필드 주입 인식, (3) MyBatis 인터페이스→resources XML 네임스페이스 조인, (4) **컨트롤러 파일은 청크당 1회** 가산·EP에는 핸들러 span만, (5) 매칭 실패 시 조용한 0 대신 `unresolved` 플래그+보수 prior |
| K4 | Gem#3, G#6(gpt), S#6 | 고정 K 그리드 vs 컨트롤러 친화 bin-packing 충돌, 사후 벽 위반 | 최적화 단위를 **파티션**으로 통일: 컨트롤러 묶음 → δ̂ 기준 First-Fit-Decreasing → 시뮬레이션으로 파티션 비용 평가, W_target 그리드. 위반 시 로컬 재분할 루프. 보고서 진실원은 K가 아니라 `len(chunks)`+est_cost |
| K5 | S#5, G#4(gpt) | W_eff=150K이 자체 실측(178~257K 게이트 통과)과 모순 | 이층 벽: `W_hard`(모델 상한) / `W_soft`(품질 정책, 기본=실측 종료 분포 p50에서 역산한 180K). 150K는 보수 프리셋 옵션. `K*_cost` vs `K*_wall` 병기 |
| K6 | Gem#4, S#2, G#5(gpt) | hold-in 검증 순환·±40%는 통과 보장 | 수용 기준 교체: (1) **N hold-out** — N=1 데이터만으로 fit → N=8 예측이 실측 3회 범위 내, (2) run leave-one-out 예측구간 커버리지 ≥2/3, (3) 트랜스크립트 Σreads 재구성 오차 보고, (4) 절대 USD 비보증 명시(상대 비교가 주 용도) |
| K7 | Gem#5 | 벽시계·병렬 트레이드오프 누락 | 시간 모델 추가: `wall ≈ Σturns × latencȳ + Σout/ratē` (셀별 실측 캘리브레이션). `--parallel`: wall=max(청크), cache_write 할증 |
| K8 | Gem#8 | 공유 환경분석 1회 비용 누락 | 총비용 = `C_env_prep + Σ_chunk C(chunk)` 명시 |
| K9 | G#7(gpt) | 미캘리브레이션 셀(haiku)을 확정값처럼 출력 | 데이터 있는 셀만 수치 출력, 없는 셀은 `insufficient_calibration`. 가격표·캐시 가정을 calibration.json에 버전과 함께 포함. (haiku는 Phase 5 완료 시 캘리브레이션 추가) |
| K10 | G#8(gpt) | bytes/4 가짜 정밀도 | w_i는 절대 토큰이 아니라 **분위수(tertile)·상대 비율**로 제시, bytes/4는 fallback임을 보고서에 고지 |

## 부분 반영

| # | 출처 | 지적 | 판단 |
|---|------|------|------|
| P1 | G#5(gpt), S#2 | 외부 프로젝트 실측 비용 검증 필수 | 실측 run은 비용이 들어 도구 PR 범위 밖. graph-rag samples/order-service에 대한 **스캔·플랜 스모크**(크래시·제약 위반 없음)까지만 포함하고, 외부 실측은 후속 실험으로 명시 |
| P2 | G#8(gpt) | micro-w_i 대신 ledger 회귀만 쓰라 | 채택하지 않되 절충: 주 예측은 캘리브레이션 셀 파라미터 기반, w_i는 공변량·순위 용도로 제한 (K10과 일관) |
