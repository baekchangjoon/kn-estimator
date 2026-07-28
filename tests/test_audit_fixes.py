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
    """haiku 윈도우는 200K다 — 사용자가 벽을 더 올려도 모델 상한을 넘길 수 없고,
    W_soft도 유효 W_hard로 캡돼 플랜은 (불가능 판정이 아니라) 상한 안에서 나온다."""
    cal = _cal()
    p = plan.build_plan(_slices(40), cal, label="template", mdl="haiku",
                        w_soft=400_000, w_hard=900_000)
    assert not p.get("status"), p
    assert p["w_hard"] <= 180_000, p["w_hard"]
    assert p["w_soft"] <= p["w_hard"]
    assert all(c["est_peak_context"] <= p["w_hard"] for c in p["chunks"])


def test_infeasible_reason_distinguishes_user_limit_from_model_cap():
    """벽 불가능 판정의 처방이 실행 가능해야 한다: 사용자 값이 병목이면 --w-hard 상향을,
    모델 캡이 병목이면 (올려도 소용없으므로) 모델 교체를 권해야 한다."""
    cal = _cal()
    # 사용자 값(50K)이 캡(900K)보다 작아 병목 → --w-hard 상향 권고가 유효
    p_user = plan.build_plan(_slices(4), cal, label="template", mdl="sonnet",
                             w_soft=180_000, w_hard=50_000)
    assert p_user["status"] == "infeasible_w_hard"
    assert "올리거나" in p_user["reason"]
    # 모델 캡이 병목인 경우: --w-hard 상향은 no-op이므로 상향 권고 대신 무효 고지
    tiny = {**cal, "cells": {"template/haiku": {**cal["cells"]["template/haiku"],
                                                "delta_env": 400_000.0}}}
    p_cap = plan.build_plan(_slices(4), tiny, label="template", mdl="haiku",
                            w_soft=500_000, w_hard=900_000)
    assert p_cap["status"] == "infeasible_w_hard"
    assert "올리거나" not in p_cap["reason"]
    assert "무효" in p_cap["reason"]
    assert p_cap["requested_w_hard"] == 900_000
    # 경계: 사용자가 캡과 같은 값을 명시해도 병목은 모델 캡 — 상향 권고는 no-op이다
    p_eq = plan.build_plan(_slices(4), tiny, label="template", mdl="haiku",
                           w_soft=500_000, w_hard=180_000)
    assert p_eq["status"] == "infeasible_w_hard"
    assert "올리거나" not in p_eq["reason"], p_eq["reason"]


def test_w_hard_default_unchanged_for_1m_window_models():
    """1M 윈도우 모델(sonnet)은 기존 기본값 900K가 그대로다 — 회귀 방지."""
    cal = _cal()
    p = plan.build_plan(_slices(12, per_controller=3), cal,
                        label="template", mdl="sonnet")
    assert p["w_hard"] == 900_000


# ---- #7 --parallel 매트릭스 일관성 -------------------------------------------

def test_matrix_uses_same_parallel_assumption_as_plan():
    """매트릭스의 셀 비용은 같은 실행 가정(parallel)의 build_plan 총액과 같아야 한다."""
    cal = _cal()
    sls = _slices(12, per_controller=3)
    m = cli.build_matrix(sls, cal, w_soft=180_000, w_hard=900_000, parallel=True)
    p = plan.build_plan(sls, cal, label="template", mdl="sonnet",
                        w_soft=180_000, w_hard=900_000, parallel=True)
    assert m["template/sonnet"]["total_cost_usd"] == p["total_cost_usd"]


def test_plan_interval_applies_parallel_surcharge():
    """예측구간도 권장 플랜과 같은 실행 가정을 써야 한다 — 병렬 1.05× 할증이
    총액에는 붙고 구간 하한에는 안 붙으면 같은 보고서 안에서 가정이 갈린다."""
    cal = _cal()
    sls = _slices(30, per_controller=3)
    args = dict(label="template", mdl="sonnet", w_soft=180_000, w_hard=900_000)
    p_seq = plan.build_plan(sls, cal, **args, parallel=False)
    p_par = plan.build_plan(sls, cal, **args, parallel=True)
    i_seq = cli._plan_interval(cal, "template", "sonnet", sls, p_seq, 180_000)
    i_par = cli._plan_interval(cal, "template", "sonnet", sls, p_par, 180_000,
                               parallel=True)
    # 허용 오차: build_plan 총액이 2자리 반올림이라 min/max 결합에 ~1e-4 편차가 남는다
    assert abs(i_par[0] / i_seq[0] - 1.05) < 1e-3, (i_seq, i_par)
    assert abs(i_par[1] / i_seq[1] - 1.05) < 1e-3


# ---- #5 calibrate silent drop ------------------------------------------------

def _write_run(tmp_path, run_id, cell, n, cost, turns=10):
    label, mdl = cell.split("/")
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
    return {"run_id": run_id, "label": label, "model": mdl, "role": "run_total",
            "n": n, "rep": 1, "gate": "pass", "wall_s": 600,
            "cost_usd": cost, "output_tokens": 40000}


def test_single_n_cell_without_reference_is_reported_not_silently_dropped(tmp_path):
    """단일 N 셀은 env/ep 2점 분해가 불가해 산출되지 않는다 — 아무 표시 없이
    사라지지 않고 skipped_cells에 사유가 남아야 한다."""
    rows = [_write_run(tmp_path, "t-n5-r1", "template/sonnet", 5, 8.1),
            _write_run(tmp_path, "t-n5-r2", "template/sonnet", 5, 9.3)]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    assert "template/sonnet" not in cal["cells"]
    assert "template/sonnet" in cal.get("skipped_cells", {})


def test_below_min_runs_cell_is_reported(tmp_path):
    """표본 부족(run<2) 스킵도 같은 채널로 보고한다."""
    rows = [_write_run(tmp_path, "t-n5-r1", "template/sonnet", 5, 8.1)]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    assert "template/sonnet" in cal.get("skipped_cells", {})


def test_all_gate_failed_cell_is_reported(tmp_path):
    """가장 흔한 무플래그 drop — 셀의 run 전부가 게이트 실패(캠페인 실측: petclinic
    haiku 0/6)면 groups에 아예 안 들어와 조용히 사라졌다. 사유가 남아야 한다."""
    ok = [_write_run(tmp_path, "f-n1-r1", "flat/opus", 1, 3.0),
          _write_run(tmp_path, "f-n1-r2", "flat/opus", 1, 3.2),
          _write_run(tmp_path, "f-n8-r1", "flat/opus", 8, 10.0),
          _write_run(tmp_path, "f-n8-r2", "flat/opus", 8, 11.0)]
    bad = [_write_run(tmp_path, "h-n8-r1", "template/haiku", 8, 0.3),
           _write_run(tmp_path, "h-n8-r2", "template/haiku", 8, 0.4)]
    for r in bad:
        r["gate"] = "fail"
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in ok + bad))
    cal = calibrate(ledger, tmp_path / "runs")
    assert "template/haiku" not in cal["cells"]
    assert "gate" in cal["skipped_cells"].get("template/haiku", ""), cal["skipped_cells"]


def test_missing_transcript_is_annotated_not_misreported(tmp_path):
    """트랜스크립트 파일 부재로 run이 빠졌으면 사유에 그 사실이 병기돼야 한다 —
    순수 표본 부족(insufficient_runs)으로 오보하면 진단 채널이 오진을 낸다."""
    rows = [_write_run(tmp_path, "t-n5-r1", "template/sonnet", 5, 8.1),
            _write_run(tmp_path, "t-n5-r2", "template/sonnet", 5, 9.3)]
    (tmp_path / "runs" / "t-n5-r2" / "transcript.jsonl").unlink()
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate(ledger, tmp_path / "runs")
    why = cal["skipped_cells"].get("template/sonnet", "")
    assert "missing_transcript" in why, why


def test_matrix_surfaces_skip_reason():
    """--calibration으로 받은 파일에 skipped_cells 사유가 있으면 매트릭스가
    맨 insufficient_calibration 대신 그 사유를 병기해야 한다."""
    cal = _cal()
    cal["skipped_cells"] = {"flat/haiku": "no_usable_runs(gate_fail=3)"}
    m = cli.build_matrix(_slices(6, per_controller=3), cal,
                         w_soft=180_000, w_hard=900_000)
    assert "no_usable_runs" in m["flat/haiku"], m["flat/haiku"]


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
    # k_cost는 브루트포스 argmin과 일치해야 한다 (조기 종료 회귀 방지 —
    # `1 <= k_cost` 같은 단정은 초기값 때문에 항진식이라 커버리지가 없다)
    from kn_estimator import model
    brute = min(range(1, ks["k_wall"] + 1),
                key=lambda k: model.simulate_chunk(
                    cal, "template", "sonnet", [1.0] * k)["cost_usd"] / k)
    assert ks["k_cost"] == brute
    # 미캘리브레이션 셀은 None
    assert plan.k_stars(cal, "flat", "haiku", w_soft=180_000) is None
    # 퇴화 계수(delta_ep=0, 단일 N approx 경로에서 가능): g가 단조 감소하므로
    # k_cost는 k_wall이어야 한다 (죽지도, 임의 상한에서 절단하지도 않는다)
    degen = {**cal, "cells": {"template/sonnet": {**cal["cells"]["template/sonnet"],
                                                  "delta_ep": 0.0}}}
    ks_d = plan.k_stars(degen, "template", "sonnet", w_soft=180_000)
    assert ks_d["k_cost"] == ks_d["k_wall"]


def test_k_cost_is_exact_even_when_optimum_is_deep():
    """calibrate의 max(ep,1) 클램프는 delta_ep=1 셀을 실제로 만든다(컨텍스트 미성장
    run). 그때 k_wall이 수천이 되는데, 탐색을 임의 상한에서 절단해 틀린 K*를 조용히
    보고하면 안 된다 — 닫힌 형태 최적값과 브루트포스가 일치해야 한다."""
    from kn_estimator import model
    cal = _cal()
    cell = {**cal["cells"]["template/sonnet"], "delta_ep": 30.0, "tau_ep": 1.0}
    deep = {**cal, "cells": {"template/sonnet": cell}}
    # w_soft를 셀 env에 상대적으로 잡아 동봉 계수가 바뀌어도 시나리오(용량≈2000·
    # 최적점>500)가 유지되게 한다
    w_soft = int(cell["S0"] + cell["delta_env"] + 60_000)
    ks = plan.k_stars(deep, "template", "sonnet", w_soft=w_soft)
    assert ks["k_wall"] > 500 and ks["k_cost"] > 500   # 최적점이 구 500 캡 너머
    brute = min(range(1, min(ks["k_wall"], 2_000) + 1),
                key=lambda k: model.simulate_chunk(
                    deep, "template", "sonnet", [1.0] * k)["cost_usd"] / k)
    assert ks["k_cost"] == brute, (ks, brute)


def test_report_shows_effective_walls_not_requested(tmp_path, monkeypatch):
    """N1: 보고서·플랜의 벽 표시는 build_plan이 실제로 쓴 유효값이어야 한다 —
    haiku에 --w-soft 400000을 주면 유효 W_soft는 180K인데 400,000을 인쇄하면
    soft 벽이 hard 벽보다 큰 자기모순 보고서가 나온다."""
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
                        ["kn-estimate", root, "--label", "template", "--model", "haiku",
                         "--w-soft", "400000"])
    cli.main()
    report = (Path(root) / ".kn" / "kn-report.md").read_text()
    wall_line = next(l for l in report.splitlines() if l.startswith("- 벽:"))
    assert "W_soft=180,000" in wall_line, wall_line
    assert "W_soft=400,000" not in wall_line
    assert "캡" in report   # 요청값과 다르면 캡 사실을 고지한다
    pj = json.loads((Path(root) / ".kn" / "kn-plan.json").read_text())
    assert pj["w_soft"] <= pj["w_hard"] == 180_000


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
                        ["kn-estimate", root, "--label", "template", "--model", "sonnet"])
    cli.main()
    report = (Path(root) / ".kn" / "kn-report.md").read_text()
    assert "K*_cost" in report and "K*_wall" in report
