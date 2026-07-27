"""탐색 테스트 매트릭스의 회귀 고정 (2026-07-27 독푸딩 리포트 T1~T29).

결함 수리는 test_dogfood_fixes.py가 고정한다. 이 파일은 **문제를 찾아낸 탐색
방법**(엣지·오용 시나리오)을 그대로 테스트로 옮겨, 당시 "양호"로 판정된 동작이
조용히 퇴행하는 것을 막는다. 각 테스트명의 T번호는 리포트 매트릭스 행이다.
"""
import json
import sys
import textwrap
from importlib import resources
from pathlib import Path

import pytest

from kn_estimator import cli


def _project(tmp_path, name="proj"):
    p = tmp_path / name / "src/main/java/com/x/PingController.java"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent("""\
        package com.x;

        @RestController
        public class PingController {
            @GetMapping("/ping")
            public ResponseEntity<String> ping() { return ResponseEntity.ok("pong"); }
        }
    """))
    return str(tmp_path / name)


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["kn-estimate", *argv])
    cli.main()


def test_t2_empty_directory_reports_no_endpoints_without_artifacts(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(empty)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert "No JSON endpoints" in capsys.readouterr().out
    assert not (empty / ".kn").exists()


def test_t3_non_spring_project_is_a_clean_zero(tmp_path, monkeypatch):
    """자바가 아예 없는 파이썬 프로젝트류 — 크래시 없이 EP 0 안내."""
    (tmp_path / "pyproj").mkdir()
    (tmp_path / "pyproj/setup.py").write_text("print('hi')\n")
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(tmp_path / "pyproj")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert not (tmp_path / "pyproj/.kn").exists()


def test_t5_broken_calibration_json_names_parse_failure(tmp_path, monkeypatch):
    root = _project(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--calibration", str(bad)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "파싱" in str(e.value)


def test_t6_cell_less_calibration_is_rejected(tmp_path, monkeypatch):
    root = _project(tmp_path)
    hollow = tmp_path / "hollow.json"
    hollow.write_text('{"version": "x", "cells": {}}')
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--calibration", str(hollow)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "셀" in str(e.value)


def test_t7_t9_bundled_calibrations_produce_different_plans(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    costs = {}
    for name in ("auth-user", "petclinic", "community"):
        _run(monkeypatch, root, "--calibration", name,
             "--out-dir", f".kn-{name}")
        costs[name] = capsys.readouterr().out.splitlines()[0]
    # 계수가 다르므로 최소 두 셋은 서로 다른 추정이 나와야 한다
    assert len(set(costs.values())) >= 2, costs


def test_t12_w_hard_too_small_is_infeasible_not_crash(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv", ["kn-estimate", root, "--w-hard", "1000"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert "infeasible_w_hard" in capsys.readouterr().out


def test_t13_soft_above_hard_is_capped_and_disclosed(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _run(monkeypatch, root, "--w-soft", "800000", "--w-hard", "400000")
    report = (Path(root) / ".kn/kn-report.md").read_text()
    assert "W_soft=400,000" in report
    assert "캡" in report


def test_t16_option_combination_smoke(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    _run(monkeypatch, root, "--conservative", "--parallel", "--groups")
    out = capsys.readouterr().out
    assert "그룹1(" in out and "할증" in out


def test_t17_t18_out_dir_accepts_paths(tmp_path, monkeypatch):
    """문서화된 동작: --out-dir는 이름뿐 아니라 상대/절대 경로도 받는다."""
    root = _project(tmp_path)
    absolute = tmp_path / "abs-out"
    _run(monkeypatch, root, "--out-dir", str(absolute))
    assert (absolute / "kn-plan.json").exists()
    _run(monkeypatch, root, "--out-dir", "../sibling-out")
    assert (tmp_path / "sibling-out/kn-plan.json").exists()


def test_t19_repeat_runs_are_byte_identical(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _run(monkeypatch, root, "--out-dir", ".kn-a")
    _run(monkeypatch, root, "--out-dir", ".kn-b")
    for f in ("kn-plan.json", "kn-report.md"):
        a = (Path(root) / ".kn-a" / f).read_bytes()
        b = (Path(root) / ".kn-b" / f).read_bytes()
        assert a == b, f


def test_t20_existing_out_dir_files_are_preserved(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = Path(root) / ".kn"
    out.mkdir()
    marker = out / "user-note.txt"
    marker.write_text("keep me")
    _run(monkeypatch, root)
    assert marker.read_text() == "keep me"
    assert (out / "kn-plan.json").exists()


def test_t26_haiku_walls_are_model_capped(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _run(monkeypatch, root, "--model", "haiku")
    pj = json.loads((Path(root) / ".kn/kn-plan.json").read_text())
    assert pj["w_hard"] == 180_000
    assert pj["w_soft"] <= 180_000


def test_bundled_default_calibration_is_loadable_resource():
    """설치본 어디서든 동봉 번들 3종이 리소스로 실존해야 한다 (독푸딩의 실존 확인)."""
    for name in ("calibration.json", "calibration-petclinic.json",
                 "calibration-community.json"):
        data = json.loads((resources.files("kn_estimator") / "data" / name).read_text())
        assert data["cells"], name
