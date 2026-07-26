"""2026-07-26 이론-구현 대조 감사에서 발견된 격차의 TDD 고정.

#1 모델별 w_hard 미분화 — haiku(200K 윈도우) 셀에도 1M 전제 900K가 적용된다.
#2(일부) 설계 v2.3이 약속한 K*_cost/K*_wall 보고서 병기 미구현.
#5 calibrate: 단일 N 셀 + flat/opus 기준 부재 시 셀이 무플래그로 사라진다.
#6 scan: 한 슬라이스에서 두 DAO가 같은 MyBatis XML을 참조하면 이중 가산된다.
#7 cli: --parallel 시 매트릭스가 parallel 미전달로 권장 플랜과 비용이 어긋난다.
"""
import json
import sys
import textwrap
from importlib import resources
from pathlib import Path

from kn_estimator import cli, plan, scan
from kn_estimator.calibrate import calibrate


def _cal():
    return json.loads(
        (resources.files("kn_estimator") / "data/calibration.json").read_text())


def _slices(n, per_controller=1, w=2000):
    return [{"endpoint": {"method": "GET", "path": f"/e{i}",
                          "controller": f"C{i // per_controller}"},
             "w_tokens": w} for i in range(n)]


def _project(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return str(tmp_path)


# ---- #1 모델별 w_hard --------------------------------------------------------

def test_w_hard_is_capped_by_model_context_window():
    """haiku 윈도우는 200K다 — 사용자가 벽을 더 올려도 모델 상한을 넘길 수 없어야
    하고, 산출 플랜의 어떤 청크도 그 상한을 넘는 peak_context를 가질 수 없다."""
    cal = _cal()
    p = plan.build_plan(_slices(40), cal, mode="template", mdl="haiku",
                        w_soft=400_000, w_hard=900_000)
    if p.get("status"):
        assert p["status"] == "infeasible_w_hard"
    else:
        assert p["w_hard"] <= 180_000, p["w_hard"]
        assert all(c["est_peak_context"] <= p["w_hard"] for c in p["chunks"])


def test_w_hard_default_unchanged_for_1m_window_models():
    """1M 윈도우 모델(sonnet)은 기존 기본값 900K가 그대로다 — 회귀 방지."""
    cal = _cal()
    p = plan.build_plan(_slices(12, per_controller=3), cal,
                        mode="template", mdl="sonnet")
    assert p["w_hard"] == 900_000


# ---- #7 --parallel 매트릭스 일관성 -------------------------------------------

def test_matrix_uses_same_parallel_assumption_as_plan():
    """매트릭스의 셀 비용은 같은 실행 가정(parallel)의 build_plan 총액과 같아야 한다."""
    cal = _cal()
    sls = _slices(12, per_controller=3)
    m = cli.build_matrix(sls, cal, w_soft=180_000, w_hard=900_000, parallel=True)
    p = plan.build_plan(sls, cal, mode="template", mdl="sonnet",
                        w_soft=180_000, w_hard=900_000, parallel=True)
    assert m["template/sonnet"]["total_cost_usd"] == p["total_cost_usd"]


# ---- #5 calibrate silent drop ------------------------------------------------

def _write_run(tmp_path, run_id, variant, n, cost, turns=10):
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
    return {"run_id": run_id, "variant": variant, "role": "run_total",
            "n": n, "rep": 1, "gate": "pass", "wall_s": 600,
            "cost_usd": cost, "output_tokens": 40000}


def test_single_n_cell_without_reference_is_reported_not_silently_dropped(tmp_path):
    """flat/opus 기준이 없는 원장에서 단일 N 셀이 아무 표시 없이 사라지면, 사용자는
    원인 불명의 insufficient_calibration만 보게 된다. skipped_cells에 사유를 남겨야 한다."""
    rows = [_write_run(tmp_path, "t-n5-r1", "flat_template_sonnet", 5, 8.1),
            _write_run(tmp_path, "t-n5-r2", "flat_template_sonnet", 5, 9.3)]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    assert "template/sonnet" not in cal["cells"]
    assert "template/sonnet" in cal.get("skipped_cells", {})


def test_below_min_runs_cell_is_reported(tmp_path):
    """표본 부족(run<2) 스킵도 같은 채널로 보고한다."""
    rows = [_write_run(tmp_path, "t-n5-r1", "flat_template_sonnet", 5, 8.1)]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    assert "template/sonnet" in cal.get("skipped_cells", {})


# ---- #6 공유 MyBatis XML 이중 가산 -------------------------------------------

def test_shared_mybatis_xml_counted_once_per_slice(tmp_path):
    """두 DAO가 같은 네임스페이스 XML을 참조해도 슬라이스에는 1회만 (JPA 엔티티
    dedup과 동일한 규칙)."""
    root = _project(tmp_path, {
        "src/main/java/com/x/OrderController.java": """\
            package com.x;

            @RestController
            public class OrderController {
                private final AlphaDao alphaDao;
                private final BetaDao betaDao;

                @GetMapping("/orders")
                public ResponseEntity<Order> list() {
                    return ResponseEntity.ok(alphaDao.get());
                }
            }
        """,
        "src/main/java/com/x/AlphaDao.java": """\
            package com.x;

            public class AlphaDao {
                public Order get() { return sqlSession.selectOne("shared.getA", 1); }
            }
        """,
        "src/main/java/com/x/BetaDao.java": """\
            package com.x;

            public class BetaDao {
                public Order get() { return sqlSession.selectOne("shared.getB", 1); }
            }
        """,
        "src/main/resources/mapper/shared.xml": """\
            <mapper namespace="shared">
              <select id="getA">SELECT 1</select>
              <select id="getB">SELECT 2</select>
            </mapper>
        """})
    sls = scan.build_slices(root, scan.inventory(root))
    assert len(sls) == 1
    s = sls[0]
    xml_rel = "src/main/resources/mapper/shared.xml"
    assert s["files"].count(xml_rel) == 1, s["files"]
    expected = (s["handler_tokens"]
                + scan.tokens_of(Path(root) / "src/main/java/com/x/AlphaDao.java")
                + scan.tokens_of(Path(root) / "src/main/java/com/x/BetaDao.java")
                + scan.tokens_of(Path(root) / xml_rel))
    assert s["w_tokens"] == expected, (s["w_tokens"], expected)


# ---- #2 K*_cost / K*_wall ----------------------------------------------------

def test_k_stars_reports_cost_and_wall_optima():
    """설계 v2.3: 보고서에 병기할 K*_cost(셀 단가 최소 K)와 K*_wall(W_soft 용량 상한 K)."""
    cal = _cal()
    ks = plan.k_stars(cal, "template", "sonnet", w_soft=180_000)
    cell = cal["cells"]["template/sonnet"]
    expected_wall = max(int((180_000 - cell["S0"] - cell["delta_env"])
                            // cell["delta_ep"]), 1)
    assert ks["k_wall"] == expected_wall
    assert 1 <= ks["k_cost"]
    # 미캘리브레이션 셀은 None
    assert plan.k_stars(cal, "flat", "haiku", w_soft=180_000) is None


def test_report_includes_k_star_line(tmp_path, monkeypatch):
    root = _project(tmp_path, {
        "src/main/java/com/x/PingController.java": """\
            package com.x;

            @RestController
            public class PingController {
                @GetMapping("/ping")
                public ResponseEntity<String> ping() { return ResponseEntity.ok("pong"); }
            }
        """})
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--mode", "template", "--model", "sonnet"])
    cli.main()
    report = (Path(root) / ".kn" / "kn-report.md").read_text()
    assert "K*_cost" in report and "K*_wall" in report
