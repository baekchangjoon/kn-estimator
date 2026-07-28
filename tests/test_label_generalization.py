"""--label 범용화 수용 테스트 (2026-07-28).

--mode(고정 enum: 실험의 생성 전략 이름)를 --label(자유 문자열)로 교체한다 —
비용 모델에게 이 축은 "계수를 잰 조건의 이름표"일 뿐이므로, 임의 작업(예: 클래스
분석)에 자기 라벨로 캘리브레이션·플랜이 돌아야 한다. 하위호환 없음(정규 배포 전).
원장도 레거시 variant 명칭 대신 label/model 명시 필드를 쓴다.
"""
import json
import sys
import textwrap
from pathlib import Path

import pytest

from kn_estimator import calibrate, cli, model


def _project(tmp_path):
    p = tmp_path / "proj/src/main/java/com/x/PingController.java"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent("""\
        package com.x;

        @RestController
        public class PingController {
            @GetMapping("/ping")
            public ResponseEntity<String> ping() { return ResponseEntity.ok("pong"); }
        }
    """))
    return str(tmp_path / "proj")


def _write_run(tmp_path, run_id, label, mdl, n, cost, turns=10):
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
            "n": n, "rep": int(run_id[-1]), "gate": "pass", "wall_s": 600,
            "cost_usd": cost, "output_tokens": 40000}


def test_calibrate_builds_cells_from_label_and_model(tmp_path):
    """원장의 label/model 필드로 셀이 만들어진다 — 임의 라벨(analyze)이어야 한다."""
    rows = [_write_run(tmp_path, "a-n1-r1", "analyze", "sonnet", 1, 3.0),
            _write_run(tmp_path, "a-n1-r2", "analyze", "sonnet", 1, 3.2),
            _write_run(tmp_path, "a-n5-r1", "analyze", "sonnet", 5, 8.0),
            _write_run(tmp_path, "a-n5-r2", "analyze", "sonnet", 5, 9.0)]
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    cal = calibrate.calibrate(ledger, tmp_path / "runs")
    assert "analyze/sonnet" in cal["cells"]


def test_calibrate_rejects_rows_without_label_or_model(tmp_path):
    row = _write_run(tmp_path, "b-n1-r1", "analyze", "sonnet", 1, 3.0)
    del row["model"]
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(row))
    with pytest.raises(SystemExit) as e:
        calibrate.calibrate(ledger, tmp_path / "runs")
    assert "b-n1-r1" in str(e.value) and "label" in str(e.value)


def test_estimate_with_custom_label_cell(tmp_path, monkeypatch, capsys):
    """자기 라벨로 캘리브레이션한 파일로 kn-estimate --label이 완주한다 —
    '100개 클래스 분석' 류의 임의 작업 시나리오."""
    rows = [_write_run(tmp_path, "c-n1-r1", "analyze", "sonnet", 1, 3.0),
            _write_run(tmp_path, "c-n1-r2", "analyze", "sonnet", 1, 3.2),
            _write_run(tmp_path, "c-n5-r1", "analyze", "sonnet", 5, 8.0),
            _write_run(tmp_path, "c-n5-r2", "analyze", "sonnet", 5, 9.0)]
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    out = tmp_path / "cal.json"
    calibrate.main(["--ledger", str(ledger), "--runs", str(tmp_path / "runs"),
                    "--out", str(out)])
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--label", "analyze",
                         "--model", "sonnet", "--calibration", str(out)])
    cli.main()
    outtxt = capsys.readouterr().out
    assert "N=1" in outtxt
    report = (Path(root) / ".kn/kn-report.md").read_text()
    assert "analyze×sonnet" in report          # 권장 플랜 헤더가 라벨을 그대로 쓴다
    assert "analyze/sonnet" in report          # 매트릭스에 그 셀이 나온다


def test_matrix_lists_calibration_cells_not_fixed_grid(tmp_path):
    """매트릭스는 고정 그리드(flat|template × 3모델)가 아니라 캘리브레이션이
    보유한 셀 + 스킵 셀(사유)로 구성된다."""
    from importlib import resources
    cal = json.loads((resources.files("kn_estimator") / "data/calibration.json").read_text())
    cal["cells"]["analyze/sonnet"] = cal["cells"]["template/sonnet"]
    cal["skipped_cells"] = {"analyze/haiku": "no_usable_runs(gate_fail=2)"}
    sls = [{"endpoint": {"method": "GET", "path": f"/e{i}", "controller": "C"},
            "w_tokens": 2000} for i in range(3)]
    m = cli.build_matrix(sls, cal, w_hard=900_000, w_soft=330_000)
    assert "analyze/sonnet" in m               # 임의 라벨 셀 포함
    assert "no_usable_runs" in m["analyze/haiku"]   # 스킵 셀은 사유 병기
    assert "flat/opus" not in m                # 보유하지 않은 조합은 만들어내지 않는다


def test_model_layer_uses_label_vocabulary():
    """estimate_cell/simulate_chunk가 임의 라벨 셀에서 동작한다."""
    from importlib import resources
    cal = json.loads((resources.files("kn_estimator") / "data/calibration.json").read_text())
    cal["cells"]["docs-translate/haiku"] = cal["cells"]["template/haiku"]
    est = model.estimate_cell(cal, "docs-translate", "haiku", [1.0] * 4)
    assert est["cost_usd"] > 0


def test_label_with_slash_is_rejected_at_cli(tmp_path, monkeypatch):
    """셀 키가 <label>/<model>이라 라벨의 '/'는 키 파싱을 오염시킨다 — 입구에서 거부."""
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--label", "auth/analyze"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "/" in str(e.value) and "라벨" in str(e.value)


def test_label_with_slash_is_rejected_in_ledger(tmp_path):
    row = _write_run(tmp_path, "s-n1-r1", "auth/analyze", "sonnet", 1, 3.0)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(row))
    with pytest.raises(SystemExit) as e:
        calibrate.calibrate(ledger, tmp_path / "runs")
    assert "s-n1-r1" in str(e.value) and "/" in str(e.value)
