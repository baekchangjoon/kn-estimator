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
    kn-plan.json: cost_curve와 controllers 필드.

    AlphaController는 EP 12개로 단일 컨트롤러 분할 경로(_pack)를 강제한다 —
    한 컨트롤러가 여러 청크에 걸치는 경우가 이 표의 존재 이유이므로 그 경로를
    실제로 밟아야 한다."""
    from kn_estimator import scan
    alpha_methods = "\n".join(
        f'    @GetMapping("/a{i}")\n'
        f'    public ResponseEntity<String> a{i}() {{ return ResponseEntity.ok("{i}"); }}\n'
        for i in range(12))
    files = {
        "src/main/java/com/x/AlphaController.java":
            "package com.x;\n\n@RestController\npublic class AlphaController {\n"
            + alpha_methods + "}\n",
        "src/main/java/com/x/BetaController.java": textwrap.dedent("""\
            package com.x;

            @RestController
            public class BetaController {
                @GetMapping("/b1")
                public ResponseEntity<String> b1() { return ResponseEntity.ok("1"); }
            }
        """)}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", str(tmp_path), "--mode", "template",
                         "--model", "sonnet"])
    cli.main()
    report = (tmp_path / ".kn" / "kn-report.md").read_text()
    co = plan.cost_coefficients(_cal(), "template", "sonnet")
    # 계수는 곡선 문맥에 앵커해 확인한다 — 맨 숫자 부분 문자열은 다른 금액과 충돌 가능
    assert f"C(K) ≈ {co['a']:.2f} + {co['b']:.3f}·K + {co['c']:.4f}·K²" in report
    # √(a/c)는 무제약 최적값으로 표기돼야 한다 (K*_cost는 K*_wall 절단을 반영하므로
    # 값이 다를 수 있다 — 같은 보고서에서 두 수치가 모순으로 읽히면 안 된다)
    assert f"무제약 K*=√(a/c)≈{(co['a'] / co['c']) ** 0.5:.1f}" in report
    assert "## 컨트롤러 단위" in report

    pj = json.loads((tmp_path / ".kn" / "kn-plan.json").read_text())
    assert set(pj["cost_curve"]) == {"a", "b", "c"}
    ctrl = pj["controllers"]
    assert ctrl["AlphaController"]["n"] == 12
    assert ctrl["BetaController"]["n"] == 1
    # EP 총합 보존 (이중 가산·누락 방지) + 청크 인덱스 정합
    assert sum(v["n"] for v in ctrl.values()) == 13
    assert pj["n_chunks"] > 1
    for info in ctrl.values():
        assert info["chunks"], info
        assert all(0 <= i < pj["n_chunks"] for i in info["chunks"])
    # 분할된 컨트롤러는 여러 청크에 걸친다
    assert len(set(ctrl["AlphaController"]["chunks"])) > 1
    # Σw는 슬라이스 w의 컨트롤러별 합과 일치
    sls = scan.build_slices(str(tmp_path), scan.inventory(str(tmp_path)))
    expected_w = {}
    for s in sls:
        name = s["endpoint"]["controller"]
        expected_w[name] = expected_w.get(name, 0) + s["w_tokens"]
    assert {k: v["w_tokens"] for k, v in ctrl.items()} == expected_w
