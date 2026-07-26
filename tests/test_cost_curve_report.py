"""비용 곡선 계수(a,b,c)와 컨트롤러 단위 표의 보고서 표현 (2026-07-26).

C(K) = a + b·K + c·K²는 셀 캘리브레이션의 닫힌 형태 합성이다 — 보고서가 이 계수를
직접 표기하고, 컨트롤러 단위 n·Σw·배정 청크를 표로 내보낸다. 컨트롤러 "단위별
a,b,c"는 만들지 않는다 — research/unit_variance.py 검정에서 단위 분화의 유의한
근거가 없었다 (전 케이스 순열 p≥0.079).
"""
import json
import sys
import textwrap
from importlib import resources

from kn_estimator import cli, model, plan


def _cal():
    return json.loads(
        (resources.files("kn_estimator") / "data/calibration.json").read_text())


def test_cost_coefficients_reproduce_simulation():
    """a + b·K + c·K²가 simulate_chunk(균일 ŵ=1)와 수치 일치해야 한다 —
    계수가 모델의 닫힌 형태라는 주장의 직접 검증."""
    cal = _cal()
    co = plan.cost_coefficients(cal, "template", "sonnet")
    for k in (1, 5, 10):
        sim = model.simulate_chunk(cal, "template", "sonnet", [1.0] * k)["cost_usd"]
        closed = co["a"] + co["b"] * k + co["c"] * k * k
        assert abs(closed - sim) < 1e-9 * max(sim, 1), (k, closed, sim)


def test_cost_coefficients_none_for_uncalibrated_cell():
    assert plan.cost_coefficients(_cal(), "flat", "haiku") is None


def test_report_expresses_cost_curve_and_controller_units(tmp_path, monkeypatch):
    """보고서: 선택 셀의 비용 곡선(a,b,c)과 컨트롤러 단위(n·Σw·배정 청크) 표.
    kn-plan.json: cost_curve와 controllers 필드."""
    files = {
        "src/main/java/com/x/AlphaController.java": """\
            package com.x;

            @RestController
            public class AlphaController {
                @GetMapping("/a1")
                public ResponseEntity<String> a1() { return ResponseEntity.ok("1"); }

                @GetMapping("/a2")
                public ResponseEntity<String> a2() { return ResponseEntity.ok("2"); }
            }
        """,
        "src/main/java/com/x/BetaController.java": """\
            package com.x;

            @RestController
            public class BetaController {
                @GetMapping("/b1")
                public ResponseEntity<String> b1() { return ResponseEntity.ok("1"); }
            }
        """}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", str(tmp_path), "--mode", "template",
                         "--model", "sonnet"])
    cli.main()
    report = (tmp_path / ".kn" / "kn-report.md").read_text()
    assert "비용 곡선" in report
    co = plan.cost_coefficients(_cal(), "template", "sonnet")
    assert f"{co['a']:.2f}" in report and f"{co['b']:.3f}" in report \
        and f"{co['c']:.4f}" in report
    assert "## 컨트롤러 단위" in report
    assert "AlphaController" in report and "BetaController" in report

    pj = json.loads((tmp_path / ".kn" / "kn-plan.json").read_text())
    assert set(pj["cost_curve"]) == {"a", "b", "c"}
    ctrl = pj["controllers"]
    assert ctrl["AlphaController"]["n"] == 2
    assert ctrl["BetaController"]["n"] == 1
    # 배정 청크 인덱스는 실제 청크 목록과 정합해야 한다
    for info in ctrl.values():
        assert info["chunks"], info
        assert all(0 <= i < pj["n_chunks"] for i in info["chunks"])
