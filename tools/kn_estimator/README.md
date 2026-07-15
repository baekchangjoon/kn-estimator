# kn-estimator

테스트 생성 착수 **전에** 대상 Spring 프로젝트를 정적 스캔해 다음을 계산한다
(LLM 호출 없음, 수 초):

- **N**: 대상 JSON 엔드포인트 수 (경로 조인 수정판 스캐너)
- **w_i**: 엔드포인트별 작업량 (핸들러 span + DI 슬라이스 + MyBatis XML, 상대 비교용)
- **청크 플랜**: 컨트롤러 친화 FFD 파티션 (W_soft/W_hard 이층 벽), 청크 수·평균 K
- **예상 비용/시간**: 모드(flat/template) × 모델(opus/sonnet/haiku) 매트릭스

```bash
python3 estimate.py <project_root> --mode template --model sonnet
# → <project_root>/.kn/kn-report.md, kn-plan.json

python3 calibrate.py    # 실측 원장에서 캘리브레이션 재생성 (게이트 통과 run만)
python3 tests/test_kn.py
```

## 원리

실측 28+13 run에서 셀별 관측 가능량 (S0, τ, δ, out)을 env(1회)/EP(한계) 2점 분해로
캘리브레이션하고, 청크를 턴 단위로 시뮬레이션한다 (`cost = P_cr·Στ_i·C + P_cw·ΣΔC + P_out·Σout`).
w_i는 δ·out·τ의 곱셈 공변량(ŵ^α, α는 fit 또는 0.5±민감도)으로만 쓴다.
상세: `docs/superpowers/specs/2026-07-09-kn-estimator-design.md` (v2 = 3벤더 리뷰 반영).

## 주의

- **절대 USD 비보증** — 단일 프로젝트 캘리브레이션. 주 용도는 상대 비교와 청크 플랜.
- 예측구간 = α 민감도(좁은 CI) × run 분산 밴드(넓은 CI, 실측 ±30~46%).
- 미캘리브레이션 셀은 `insufficient_calibration` (게이트 통과 run 2개 미만).
- 검증: N hold-out, LOO 예측구간 커버리지(11/12), 순서 보존, 외부 프로젝트 스모크
  — `tests/test_kn.py`.
