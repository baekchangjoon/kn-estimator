# kn-estimator 독립 분리·개선 — 세션 핸드오프

이 저장소는 `reduce-token-rv`(브랜치 `exp/nimbus-v3`)의 `tools/kn_estimator/`를 **독립 도구로
분리·개선하기 위해 시딩**한 것이다 (2026-07-16, 원 실험 저장소에서 추출). 미션:
**reduce-token 저장소 결합을 제거한 스탠드얼론 도구로 만들고, 리뷰에서 남은 개선을 구현**한다.

## 1. kn-estimator가 무엇인가

테스트 생성 착수 **전에** 대상 Spring 프로젝트를 정적 스캔(LLM 호출 없음, 수 초)해
N(엔드포인트 수)·w_i(EP별 작업량)·청크 플랜(FFD)·예상 비용/시간(모드×모델 매트릭스)을 산출.
원리·요구사항: `docs/2026-07-09-kn-estimator-design.md` (v2, 3벤더 리뷰 반영),
리뷰 트리아지: `docs/2026-07-09-kn-estimator-review-triage.md` (+ raw-kn-*.txt 3종),
비용 모델 근거: `docs/cost-model-explained.md`, `docs/scaling-analysis.md`.

## 2. 시딩 상태 (검증 완료 스모크)

```bash
# 인벤토리: 167 EP (테스트 기대값과 일치)
# 캘리브레이션: 5셀 {flat/opus:6, flat/sonnet:3, template/opus:3, template/sonnet:3, template/haiku:3}
#   (flat/haiku는 세션1 당시에도 전 run 게이트 fail → insufficient_calibration이 정상)
python3.12 tools/kn_estimator/estimate.py smartplant --mode template --model sonnet
# → N=167 chunks=60 k_avg=2.8 est=$143.9, smartplant/.kn/{kn-report.md,kn-plan.json}
```

| 자산 | 위치 | 유래 |
|---|---|---|
| 도구 5모듈+테스트+GUIDE | `tools/kn_estimator/` | rv3 클론 그대로 (구조 보존 — 분리 리팩토링이 이 세션의 일) |
| 스캐너 의존 | `harness/endpoints.py` | scan.py가 `../../harness`로 import — **결합 제거 대상** |
| 캘리브레이션 데이터(정본) | `results/run_ledger.jsonl` + `results/runs/*/transcript.jsonl` + `gate-adjudications.json` | 세션1(prior-e44fa24) flat*/flat_template* 셀만 발췌 (28+13 run의 캘리브레이션 소스) |
| 보조 데이터 | `results/rv3/` | rv3 원장 + flat/template r41 트랜스크립트 (재캘리브레이션 실험용) |
| SUT | `smartplant/` (gitignore) | 7e21b18 핀, 전용 클론 — rv4 워크스페이스와 경합 없음 |

## 3. 알려진 결함 (분리 작업에서 반드시 처리)

1. **`tests/test_kn.py:6` 하드코딩 경로** `/home/baek/temp/reduce-token` — 저장소 상대 + env
   오버라이드로 교체 (rv4 워크스페이스에서 같은 부류 3건을 `RT_SUT_WS`/`RT_ENDPOINTS` 패턴으로
   수리한 전례 있음). 이 수리 전에는 테스트가 전부 깨진다.
2. **harness 결합**: `scan.py:15-16`이 `sys.path` 조작으로 `harness/endpoints.py` import —
   패키지 내부로 vendored 이동 또는 정식 모듈화.
3. **calibrate.py `__main__`의 `parents[2]` 루트 추정** — 저장소 구조가 바뀌면 깨짐. CLI 인자화.
4. **`sys.path.insert` 기반 모듈 로딩 전반** — 정식 패키지 구조(`pyproject.toml`, `pip install -e .`,
   콘솔 스크립트 엔트리포인트)로 전환.

## 4. 개선 방향 (분리 완료 후)

- 리뷰 트리아지의 미해소 항목 확인·구현 (`docs/2026-07-09-kn-estimator-review-triage.md`).
- 캘리브레이션 데이터 규격화: 원장 형식(세션1 `role=="run_total"`)이 rv 계열과 다름 —
  `results/rv3/run_ledger.jsonl`도 읽을 수 있는 어댑터 or 규격 문서화.
- 타 프로젝트 일반화: SmartPlant 외 Spring 프로젝트에서 inventory/slice가 도는지
  (README의 "절대 USD 비보증 — 단일 프로젝트 캘리브레이션" 한계 명시 유지).
- 완료 정의: 분리 후에도 스모크 기준선 재현 — **inventory 167 EP · 캘리브레이션 5셀 ·
  estimate 스모크가 시딩 시점과 동일 수치**여야 한다 (리팩토링의 행위 보존 검증).

## 5. 작업 규약

- 실행: `python3.12` (시스템 python3는 3.9라 부족할 수 있음).
- 개발 워크플로우는 `~/.claude/dev-workflow.md` 전역 규칙을 따른다 — 특히: 기능 작업은
  브랜치+워크트리, brainstorming→요구사항명세→plan 순서, TDD, PR 전 3게이트.
- 원 실험 저장소(`../reduce-token-rv3`, `../sandbox-20260715-081154`)는 **읽기 전용 참조** —
  수정 금지 (rv4 개선 작업이 별도 세션에서 진행 중).
