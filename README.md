# kn-estimator

테스트 생성에 착수하기 **전에** 대상 Spring 프로젝트를 정적 스캔해 다음을 계산한다
(LLM 호출 없음, 수 초):

- **N**: 대상 JSON 엔드포인트 수
- **w_i**: 엔드포인트별 작업량 (핸들러 span + 의존성 슬라이스 + MyBatis XML, 상대 비교용)
- **청크 플랜**: 컨트롤러 단위로 묶는 파티션, 청크 수와 평균 K
- **예상 비용/시간**: 모드(flat/template) × 모델(opus/sonnet/haiku) 매트릭스

## 설치

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

표준 라이브러리만 사용하므로 별도 의존성 설치는 없다. Python 3.9 이상이면 동작한다.

## 사용

```bash
kn-estimate <project_root> --mode template --model sonnet
# → <project_root>/.kn/kn-report.md   (사람용 보고서)
#   <project_root>/.kn/kn-plan.json   (기계용 청크 플랜)
```

주요 옵션:

| 옵션 | 뜻 |
|---|---|
| `--mode flat\|template` | 생성 모드 |
| `--model opus\|sonnet\|haiku` | 대상 모델 |
| `--calibration <path>` | 동봉 캘리브레이션 대신 다른 파일 사용 |
| `--conservative` | 청크 컨텍스트 상한을 150K로 낮춘 보수 설정 |
| `--parallel` | 청크를 병렬 실행한다고 가정하고 벽시계 계산 |
| `--out-dir <name>` | 산출물 디렉토리 이름 (기본 `.kn`) |

캘리브레이션은 패키지에 동봉돼 있어 별도 데이터 없이 바로 동작한다.

### 캘리브레이션 재생성

실측 원장에서 계수를 다시 계산할 때만 필요하다. 게이트를 통과한 run만 사용한다.

```bash
kn-calibrate --ledger results/run_ledger.jsonl \
             --runs results/runs \
             --out src/kn_estimator/data/calibration.json
```

## 테스트

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest tests/
```

대상 프로젝트(`smartplant/`)는 저장소에 포함되지 않는다. 없으면 해당 테스트는 건너뛴다.
다른 경로를 쓰려면 `KN_SUT` 환경변수로 지정한다. 원장 경로는 `KN_LEDGER`/`KN_RUNS`,
외부 프로젝트 스모크 테스트 경로는 `KN_EXTERNAL_SAMPLE`로 지정한다.

## 원리

실측 run에서 셀(모드×모델)별 관측값(시작 컨텍스트 S0, 턴 수 τ, 컨텍스트 증가 δ, 출력
토큰)을 환경 고정분과 엔드포인트 한계분으로 분해해 캘리브레이션하고, 청크를 턴 단위로
시뮬레이션한다:

```
cost = P_cache_read·Σ(τ_i·C) + P_cache_write·(S0 + Σδ_i) + P_out·Σout_i
```

w_i는 절대 토큰이 아니라 δ·out·τ의 곱셈 공변량(ŵ^α)으로만 쓴다.

배경과 수식 유도: [docs/cost-model-explained.md](docs/cost-model-explained.md).
전체 정리: [docs/2026-07-16-kn-estimator-overview.md](docs/2026-07-16-kn-estimator-overview.md).
설계: [docs/2026-07-09-kn-estimator-design.md](docs/2026-07-09-kn-estimator-design.md).

## 한계

- **절대 USD는 보증하지 않는다** — 캘리브레이션이 단일 프로젝트(SmartPlant) 실측 기반이다.
  주 용도는 모드·모델·청크 구성의 상대 비교와 청크 플랜이다.
- 캘리브레이션 근거는 이 저장소에 트랜스크립트가 있는 **18 run**이다 (셀별: flat/opus 6,
  flat/sonnet 3, template/opus 3, template/sonnet 3, template/haiku 3). 원 실험은 이보다
  많은 run을 돌렸지만 여기 포함된 것만 계수에 반영된다.
- 예측 구간은 α 민감도(좁은 구간)와 run 간 분산(넓은 구간, 실측 ±30~46%)을 결합한 값이다.
- 캘리브레이션 run이 2개 미만인 조합은 추정치를 내지 않고 `insufficient_calibration`으로
  표시한다.
- 토큰 수는 파일 크기를 4로 나눈 근사값이다.
- 정적 슬라이스는 리플렉션·동적 라우팅·설정 기반 빈을 과소평가할 수 있다.
- 작업량 w_i는 코드 **크기**만 반영하고 분기 수 같은 복잡도는 반영하지 않는다. 배경은
  [총정리 문서 §4](docs/2026-07-16-kn-estimator-overview.md)에 있다.
