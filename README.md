# kn-estimator

> 🌐 **한국어** (현재 문서) · [English](README.en.md)

<p align="center">
  <a href="https://github.com/baekchangjoon/kn-estimator/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/baekchangjoon/kn-estimator/actions/workflows/tests.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-blue">
  <img alt="Dependencies" src="https://img.shields.io/badge/dependencies-stdlib%20only-informational">
  <img alt="LLM calls" src="https://img.shields.io/badge/LLM%20calls-0-success">
</p>

**LLM으로 Spring 백엔드의 블랙박스 API 테스트를 생성하기 전에**, 대상 프로젝트를
정적 스캔(LLM 호출 없음, 수 초)해 **예상 비용·시간과 비용 최적 생성 묶음(청크
플랜)** 을 계산하는 도구입니다. 단일 세션의 2차(N²) 비용이 청크 분할로 1차(선형)가
되는 지점 — 몇 개씩, 어떤 엔드포인트끼리 묶어야 하는지 — 를 프로젝트별로
산출합니다.

```
단일 세션:  cost(N) = a + b·N + c·N²          ← 컨텍스트 누적(δ·τ)이 만드는 2차 항
청크 실행:  cost(N) ≈ N × g(K),  g(K) = a/K + b + c·K   ← K를 벽 안에 가두면 1차
            K*_cost = √(a/c)     K*_wall = (W_soft − S0 − δ_env) / δ_ep
```

## 설치

```bash
pip install git+https://github.com/baekchangjoon/kn-estimator
```

Python 3.9 이상이면 동작하고, 표준 라이브러리만 사용합니다. 개발용 설치는:

```bash
git clone https://github.com/baekchangjoon/kn-estimator && cd kn-estimator
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
```

## 빠른 시작

```bash
kn-estimate <spring-project-root> --groups
```

실행 예 (실측 출력):

```text
N=18 chunks=3 k_avg=6.0 est=$21.18

[spring-petclinic] 비용 최적 생성 묶음 (template×sonnet):
  그룹1(POST /api/reservations, GET /api/reservations/{id}, …) — $6.93, peak 295,105
  그룹2(GET /api/pets/types, GET /api/pets/{petId}, …) — $7.08, peak 301,767
  그룹3(GET /api/owners, GET /api/owners/{ownerId}, …) — $7.17, peak 305,431
위 3개 그룹을 각각 **새 독립 세션**으로 돌리세요 — 세션을 이어가면 비용이 2차로
돌아갑니다. 예상 총 $21.18, 예측구간 $15~$28.
ℹ 이 프로젝트의 자체 캘리브레이션이 없습니다 — 동봉(tainted-spring-auth-user) 계수로 추정했습니다 …
```

각 그룹을 새 독립 세션 하나로 실행하면 지출이 엔드포인트 수 N에 선형이 됩니다.
함께 생성되는 산출물:

| 산출물 | 내용 |
|---|---|
| `<root>/.kn/kn-report.md` | 사람용 보고서 — N·w 분포, 권장 플랜, 예측구간, 비용 곡선(a,b,c), K\*, 모드×모델 매트릭스, 컨트롤러 단위 표, 한계 고지 |
| `<root>/.kn/kn-plan.json` | 기계용 플랜 — 청크별 엔드포인트·예상 비용·피크 컨텍스트, `cost_curve`, `controllers` |

## 아이디어

단일 LLM 세션에서 엔드포인트를 순서대로 처리하면 두 가지가 동시에 자랍니다 —
턴 수(∝N)와 턴당 읽는 컨텍스트(누적 잔류, ∝N). 총 읽기 비용은 그 곱이라 N²에
비례합니다. 엔드포인트를 K개씩 끊어 독립 세션으로 돌리면 각 세션이 2차 곡선의
"아직 싼 앞부분"만 쓰므로 총비용이 N에 선형이 되고, 그 직선의 기울기 g(K)가
U자 곡선을 그려 최적 K가 존재합니다.

kn-estimator는 착수 전에:

1. 대상 프로젝트를 **정적 스캔**해 N(JSON 엔드포인트 수)과 엔드포인트별 작업량
   w_i(핸들러 span + 의존성 슬라이스 + MyBatis XML/JPA 엔티티)를 계산하고,
2. 실측 캘리브레이션 계수(S0/τ/δ/out, 모드×모델 셀별)로 청크를 **턴 단위
   시뮬레이션**해,
3. 비용 최적과 컨텍스트 벽 중 먼저 걸리는 제약으로 **컨트롤러 친화 bin-packing
   청크 플랜**을 산출합니다.

```
cost = P_cache_read·Σ(τ_i·C) + P_cache_write·(S0 + Σδ_i) + P_out·Σout_i
```

w_i는 절대 토큰이 아니라 δ·out·τ의 곱셈 공변량(ŵ^α)으로만 쓰므로, 절대 비용
수준은 전적으로 캘리브레이션 계수에서 옵니다 (w를 일괄 배수해도 결과 불변).
수식 유도와 실측 근거: [docs/cost-model-explained.md](docs/cost-model-explained.md).

## CLI 옵션

```bash
kn-estimate <project_root> [옵션]
```

| 옵션 | 기본 | 뜻 |
|---|---|---|
| `--mode flat\|template` | template | 생성 모드 |
| `--model opus\|sonnet\|haiku` | sonnet | 대상 모델 (미캘리브레이션 셀은 `insufficient_calibration` 표기) |
| `--groups` | off | 비용 최적 생성 묶음을 "그룹N(EP, …)" 실행 지시 형태로 출력 |
| `--calibration <path>` | 동봉본 | 자체 캘리브레이션 파일 사용 |
| `--w-soft <n>` | 330000 | 품질 정책 벽 (초과 시 패널티+경고, 유효 W_hard로 캡) |
| `--w-hard <n>` | 900000 | 모델 상한 벽 (모델별 윈도우×0.9로 자동 캡 — haiku 180K) |
| `--conservative` | off | W_soft=250K 보수 프리셋 |
| `--parallel` | off | 청크 병렬 실행 가정 (벽시계=max, cache_write 5% 할증) |
| `--out-dir <name>` | `.kn` | 산출물 디렉토리 이름 |

## 캘리브레이션

동봉 캘리브레이션(tainted-spring-auth-user 실측 17 run, 3셀 — opus 미실측)으로 별도
데이터 없이 바로 동작하고, petclinic·tainted-spring-community 캘리브레이션도
동봉돼 이름으로 선택할 수 있습니다 (`--calibration petclinic`, `--calibration community`). 단 동봉 계수는 단일
프로젝트 실측이라 **절대 USD는 보증하지 않습니다** —
다중 프로젝트 실측(54 run)에서 동봉 계수 그대로는 오차 −23~−34%, 파일럿
재캘리브레이션 후 ±10%였습니다. `--calibration` 없이 실행하면 CLI가 이 사실과
파일럿 절차를 고지합니다:

```bash
# 1) 같은 모드×모델로 크기가 다른 그룹 2개 이상 실측 (예: EP 1개짜리 + 최소 그룹)
# 2) 실측 원장으로 자체 계수 재계산
kn-calibrate --ledger run_ledger.jsonl --runs runs/ --out my-cal.json
# 3) 게이트 통과 세션의 컨텍스트 분포로 --w-soft 재산정 후 재실행
kn-estimate <root> --calibration my-cal.json --w-soft <재산정값>
```

원장 스키마와 상세 절차: [docs/GUIDE.md](docs/GUIDE.md) §4.4.

## 구성

```
src/kn_estimator/
  endpoints.py           # 엔드포인트 인벤토리 (N) — @RestController/@ResponseBody 스캔
  scan.py                # 엔드포인트별 작업량 슬라이스 (w_i) — DI 그래프 BFS, MyBatis/JPA 조인
  model.py               # 청크 비용 시뮬레이션 (턴 단위, 캐시 read/write/out 분해)
  plan.py                # 컨트롤러 친화 FFD 파티션, 이층 벽(W_soft/W_hard), K*, 비용 곡선
  calibrate.py           # 실측 원장 → 셀별 계수 (kn-calibrate)
  cli.py                 # kn-estimate — 보고서·플랜·매트릭스·그룹 출력
  data/calibration*.json # 동봉 캘리브레이션 (캠페인 실측 — 기본 auth-user + petclinic/community)
docs/                    # 가이드·수식 유도·설계·실측 캠페인·연구 노트
results/                 # 캘리브레이션 원장·트랜스크립트 (재현용 원자료)
research/                # 검정 스크립트 (w 공변량, 단위별 계수 분화)
tests/                   # pytest — SUT 없이 45건, SUT 있으면 +13건
```

## 테스트

```bash
.venv/bin/python -m pytest tests/
```

SUT(petclinic 포크)·외부 샘플 의존 테스트는 해당 경로가 없으면 건너뜁니다
(`KN_SUT`, `KN_EXTERNAL_SAMPLE`, `KN_LEDGER`/`KN_RUNS` 환경변수로 지정 가능).
나머지 45건은 환경 무관이며 CI(GitHub Actions)가 push·PR마다 실행합니다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | 동작 원리·파이프라인·CLI·파일럿 캘리브레이션 워크플로 |
| [docs/cost-model-explained.md](docs/cost-model-explained.md) | 왜 2차인가, 청크가 왜 1차로 만드나, K는 왜 중요한가 |
| [docs/2026-07-16-kn-estimator-overview.md](docs/2026-07-16-kn-estimator-overview.md) | 배경·모델·현황·개선 총정리 |
| [docs/2026-07-20-multi-project-calibration-campaign.md](docs/2026-07-20-multi-project-calibration-campaign.md) | 3개 프로젝트 실측 캠페인(54 run) — 계수 이전성·모드 역전·파일럿 검증 |
| [docs/2026-07-26-cost-curve-and-unit-coefficients.md](docs/2026-07-26-cost-curve-and-unit-coefficients.md) | 비용 곡선 계수(a,b,c) 유도와 단위별 계수 분화 검정 |

## 한계

- **절대 USD는 비보증** — 주 용도는 모드·모델·청크 구성의 상대 비교와 청크 플랜.
- 예측 구간은 α 민감도(좁은 구간) × run 간 분산(실측 ±30~46%)의 결합 — 점추정이
  아니라 구간으로 읽어야 합니다.
- 캘리브레이션 run이 2개 미만인 셀은 추정치를 내지 않고
  `insufficient_calibration (사유)`로 표기합니다.
- 토큰 수는 파일 크기/4 근사. 정적 슬라이스는 리플렉션·동적 라우팅·설정 기반
  빈을 과소평가할 수 있습니다.
- 작업량 w는 코드 크기만 반영합니다 — 분기 수 등 복잡도 미반영
  (배경: [총정리 문서 §4](docs/2026-07-16-kn-estimator-overview.md)).

## 라이선스

[MIT](LICENSE)
