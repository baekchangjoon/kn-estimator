"""캘리브레이션 일반화 결함의 TDD 고정 — 다중 프로젝트 캠페인(2026-07-17)에서 발견.

F6: calibrate.py가 measured_costs를 `n == 8` run에서만 수집한다 (SmartPlant 전제).
최대 N이 8이 아닌 프로젝트(tainted-spring: N=5)는 measured_costs가 빈 배열이 되고,
model._run_variance_band가 실측 분산 대신 기본 밴드(0.7/1.3)로 조용히 퇴화한다.
"""
import json

from kn_estimator.calibrate import calibrate


def _write_run(tmp_path, run_id, n, cost, turns=10):
    d = tmp_path / "runs" / run_id
    d.mkdir(parents=True)
    recs = []
    ctx = 50000
    for i in range(turns):
        ctx += 2000
        recs.append({"type": "assistant",
                     "message": {"id": f"{run_id}-m{i}",
                                 "usage": {"cache_read_input_tokens": ctx,
                                           "input_tokens": 10,
                                           "cache_creation_input_tokens": 500}}})
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    return {"run_id": run_id, "variant": "flat_template_sonnet", "role": "run_total",
            "n": n, "rep": 1, "gate": "pass", "wall_s": 600,
            "cost_usd": cost, "output_tokens": 40000}


def test_measured_costs_uses_largest_n_not_hardcoded_8(tmp_path):
    """최대 N이 5인 프로젝트에서도 measured_costs가 그 N의 실측 비용으로 채워져야 한다."""
    rows = [
        _write_run(tmp_path, "p_t-n1-r1", 1, 3.5),
        _write_run(tmp_path, "p_t-n1-r2", 1, 3.7),
        _write_run(tmp_path, "p_t-n5-r1", 5, 8.1),
        _write_run(tmp_path, "p_t-n5-r2", 5, 9.3),
    ]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    cell = cal["cells"]["template/sonnet"]
    assert cell["measured_costs"] == [8.1, 9.3], cell["measured_costs"]
