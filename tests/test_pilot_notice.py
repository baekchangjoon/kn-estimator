"""자체 캘리브레이션 부재 고지 (2026-07-27).

SUT 자체 캘리브레이션 없이(=동봉 LegacySut 계수로) 실행하면, 도구가 그 사실과
파일럿 캘리브레이션 절차를 명시 고지해야 한다 — 캠페인 실측에서 동봉 계수 그대로는
오차 −23~−34%, 파일럿 재캘리브레이션 후 ±10%였다. 캘리브레이션은 실측 run 원장이
필요해 도구가 자동 수행할 수는 없으므로, 고지가 자동화의 상한이다.
"""
import sys
import textwrap
from importlib import resources

from kn_estimator import cli


def _project(tmp_path):
    p = tmp_path / "src/main/java/com/x/PingController.java"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent("""\
        package com.x;

        @RestController
        public class PingController {
            @GetMapping("/ping")
            public ResponseEntity<String> ping() { return ResponseEntity.ok("pong"); }
        }
    """))
    return str(tmp_path)


def test_bundled_calibration_prints_pilot_notice(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv", ["kn-estimate", root])
    cli.main()
    out = capsys.readouterr().out
    assert "자체 캘리브레이션" in out
    assert "파일럿" in out and "kn-calibrate" in out


def test_explicit_calibration_suppresses_pilot_notice(tmp_path, monkeypatch, capsys):
    """--calibration으로 자체 계수를 물렸으면 고지하지 않는다."""
    root = _project(tmp_path)
    cal_file = tmp_path / "my-cal.json"
    cal_file.write_text(
        (resources.files("kn_estimator") / "data/calibration.json").read_text())
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--calibration", str(cal_file)])
    cli.main()
    out = capsys.readouterr().out
    assert "파일럿" not in out
