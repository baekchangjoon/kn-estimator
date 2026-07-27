"""독푸딩·탐색 테스트 리포트(2026-07-27)의 결함 수리 고정.

D1 kn-calibrate 원장 부재/깨진 줄 → raw traceback 크래시
D2 빈 원장 → exit 0으로 cells:{} 조용히 산출
D3 없는 경로/파일을 "No JSON endpoints found"로 오도
D4 번들 이름 오타 시 가용 번들 미안내
D6 보고서 매트릭스의 haiku 최저가 무캐비앗 표시
D7 벽 값 미검증(음수·0)
+ 파일럿 루프 성공 경로 통합(합성 원장 → kn-calibrate → --calibration 재실행)
"""
import json
import sys
import textwrap

import pytest

from kn_estimator import calibrate, cli


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
            "n": n, "rep": int(run_id[-1]), "gate": "pass", "wall_s": 600,
            "cost_usd": cost, "output_tokens": 40000}


# ---- D1: kn-calibrate 오류 경로 ----------------------------------------------

def test_calibrate_missing_ledger_exits_with_message(tmp_path):
    with pytest.raises(SystemExit) as e:
        calibrate.main(["--ledger", str(tmp_path / "nope.jsonl"),
                        "--runs", str(tmp_path)])
    assert "원장" in str(e.value)


def test_calibrate_broken_ledger_line_names_the_line(tmp_path):
    ledger = tmp_path / "bad.jsonl"
    ledger.write_text('{"ok": 1}\nnot-json\n')
    with pytest.raises(SystemExit) as e:
        calibrate.main(["--ledger", str(ledger), "--runs", str(tmp_path)])
    # 경로에 우연히 든 숫자로 통과하지 않도록 "<파일>:줄번호:" 형태를 정확히 고정
    assert f"{ledger}:2:" in str(e.value) and "파싱" in str(e.value)


# ---- D2: 빈 원장 → 비-0 exit --------------------------------------------------

def test_calibrate_zero_usable_runs_is_an_error(tmp_path):
    ledger = tmp_path / "empty.jsonl"
    ledger.write_text("")
    out = tmp_path / "cal.json"
    with pytest.raises(SystemExit) as e:
        calibrate.main(["--ledger", str(ledger), "--runs", str(tmp_path),
                        "--out", str(out)])
    assert "0건" in str(e.value)
    assert not out.exists()   # 빈 캘리브레이션 파일을 남기지 않는다


# ---- D3: 경로 오류 메시지 -----------------------------------------------------

def test_missing_project_root_names_the_real_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(tmp_path / "no-such")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "디렉토리" in str(e.value) or "경로" in str(e.value)


def test_file_as_project_root_names_the_real_cause(tmp_path, monkeypatch):
    f = tmp_path / "a-file"
    f.write_text("x")
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(f)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "디렉토리" in str(e.value)


# ---- D4: 번들 이름 오타 → 가용 번들 안내 --------------------------------------

def test_unknown_calibration_name_lists_bundles(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--calibration", "nosuchbundle"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    msg = str(e.value)
    assert "petclinic" in msg and "community" in msg and "auth-user" in msg


# ---- D6: 보고서 haiku 캐비앗 --------------------------------------------------

def test_report_matrix_carries_haiku_gate_risk_caveat(tmp_path, monkeypatch):
    from pathlib import Path
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv", ["kn-estimate", root])
    cli.main()
    report = (Path(root) / ".kn" / "kn-report.md").read_text()
    assert "haiku" in report
    # 매트릭스에 haiku 셀 금액이 있으면 게이트 통과율 리스크 각주가 있어야 한다
    assert "게이트" in report and "0/6" in report


# ---- D7: 벽 값 검증 -----------------------------------------------------------

def test_nonpositive_walls_are_rejected(tmp_path, monkeypatch):
    root = _project(tmp_path)
    for flag, val in (("--w-soft", "-100"), ("--w-soft", "0"), ("--w-hard", "0")):
        monkeypatch.setattr(sys, "argv", ["kn-estimate", root, flag, val])
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert "양수" in str(e.value), (flag, val, str(e.value))


# ---- 파일럿 루프 성공 경로 (리뷰 공백 보완) ------------------------------------

def test_pilot_loop_end_to_end(tmp_path, monkeypatch, capsys):
    """GUIDE §4.4 스키마대로 합성 원장을 만들어 kn-calibrate → 산출 파일로
    kn-estimate --calibration 재실행까지 성공해야 한다."""
    from pathlib import Path
    rows = [_write_run(tmp_path, "p_t-n1-r1", "flat_template_sonnet", 1, 3.5),
            _write_run(tmp_path, "p_t-n1-r2", "flat_template_sonnet", 1, 3.7),
            _write_run(tmp_path, "p_t-n3-r1", "flat_template_sonnet", 3, 6.1),
            _write_run(tmp_path, "p_t-n3-r2", "flat_template_sonnet", 3, 7.0)]
    ledger = tmp_path / "run_ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    out = tmp_path / "my-cal.json"
    calibrate.main(["--ledger", str(ledger), "--runs", str(tmp_path / "runs"),
                    "--out", str(out)])
    assert json.loads(out.read_text())["cells"]

    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--calibration", str(out)])
    cli.main()
    outtxt = capsys.readouterr().out
    assert "N=1" in outtxt
    assert "파일럿" not in outtxt   # 자체 캘리브레이션이므로 고지 억제
