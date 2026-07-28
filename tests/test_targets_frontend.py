"""targets 범용 앞단 수용 E2E (REQ-001~013).

요구사항명세: docs/superpowers/requirements/2026-07-28-targets-frontend-requirements.md
design spec:  docs/superpowers/specs/2026-07-28-targets-frontend-design.md

pytest 전용 파일이다 (픽스처 사용) — test_kn.py의 자체 러너에는 넣지 않는다.
"""
import io
import json
import os
import sys
from pathlib import Path

import pytest

from kn_estimator import cli
from kn_estimator.cli import BUNDLED_CALIBRATION

REPO = Path(__file__).resolve().parent.parent
SUT = Path(os.environ.get("KN_SUT") or REPO / "petclinic")


def _run(monkeypatch, argv, cwd=None, stdin=None):
    if cwd:
        monkeypatch.chdir(cwd)
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "argv", ["kn-estimate"] + argv)
    cli.main()


def _files_list(tmp_path):
    d = tmp_path / "docs sub"          # 공백 포함 디렉터리 (REQ-004)
    d.mkdir()
    small = d / "small.md"; small.write_text("x" * 400)
    big = tmp_path / "big.md"; big.write_text("y" * 40_000)
    empty = d / "empty.md"; empty.write_text("")        # 0바이트 (REQ-004)
    lst = tmp_path / "list.txt"
    lst.write_text(f"{small}\n\n# 주석\n{big}\n{empty}\n")
    return lst


def _report(cwd):
    return (cwd / ".kn/kn-report.md").read_text()


def _plan(cwd):
    return json.loads((cwd / ".kn/kn-plan.json").read_text())


def _section(report, header):
    """header로 시작하는 섹션의 본문만 (다음 '## ' 전까지)."""
    start = report.index(header)
    body = report[start + len(header):]
    nxt = body.find("\n## ")
    return report[start:start + len(header) + (nxt if nxt != -1 else len(body))]


# ---- REQ-001 ----------------------------------------------------------------

def test_req001_text_list(tmp_path, monkeypatch, capsys):
    lst = _files_list(tmp_path)
    _run(monkeypatch, ["--targets", str(lst), "--label", "template",
                       "--model", "sonnet"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "N=3" in out
    assert (tmp_path / ".kn/kn-report.md").exists()     # out-dir는 cwd 기준
    assert (tmp_path / ".kn/kn-plan.json").exists()


# ---- REQ-002 ----------------------------------------------------------------

def test_req002_stdin(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, ["--targets", "-"], cwd=tmp_path, stdin="a\nb\nc\n")
    out = capsys.readouterr().out
    assert "N=3" in out
    assert "(stdin, 3건)" in _report(tmp_path)


def test_req002_stdin_json_rejected(tmp_path, monkeypatch, capsys):
    payload = json.dumps([{"id": "a"}])
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", "-"], cwd=tmp_path, stdin=payload)
    assert "텍스트 목록 전용" in str(e.value)


# ---- REQ-003 ----------------------------------------------------------------

def test_req003_n_only(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, ["--n", "100", "--label", "template", "--model", "sonnet"],
         cwd=tmp_path)
    out = capsys.readouterr().out
    assert "N=100" in out
    assert "k_avg=" in out and "est=$" in out
    plan_text = (tmp_path / ".kn/kn-plan.json").read_text()
    assert "unit-001" in plan_text and "unit-100" in plan_text


# ---- REQ-004 ----------------------------------------------------------------

def test_req004_auto_w_group(tmp_path, monkeypatch, capsys):
    lst = _files_list(tmp_path)
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    report = _report(tmp_path)
    assert "## w 상위 10 대상" in report
    # 표 최상단 = 가장 큰 파일
    table_start = report.index("## w 상위 10 대상")
    first_row = [ln for ln in report[table_start:].splitlines()
                 if ln.startswith("|") and ".md" in ln][0]
    assert "big.md" in first_row
    # 같은 디렉터리(docs sub)의 파일 2개가 같은 그룹 행으로 집계
    grp_rows = [ln for ln in _section(report, "## 그룹 단위").splitlines()
                if ln.startswith("|") and "docs sub" in ln]
    assert len(grp_rows) == 1 and "| 2 |" in grp_rows[0]
    # 0바이트 파일도 w>=1로 포함 (N=3이 이미 검증하지만 명시 확인)
    assert "empty.md" in report


def test_req004_absolute_path_outside_cwd(tmp_path, tmp_path_factory,
                                          monkeypatch, capsys):
    outside = tmp_path_factory.mktemp("outside")
    a = outside / "a.txt"; a.write_text("x" * 1000)
    b = outside / "b.txt"; b.write_text("y" * 2000)
    lst = tmp_path / "list.txt"
    lst.write_text(f"{a}\n{b}\n")
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "N=2" in out
    assert str(outside) in _report(tmp_path)   # group = 절대 경로의 부모 디렉터리


# ---- REQ-005 ----------------------------------------------------------------

def test_req005_partial_fallback(tmp_path, monkeypatch, capsys):
    f1 = tmp_path / "f1.txt"; f1.write_text("aaa")
    f2 = tmp_path / "f2.txt"; f2.write_text("bbb")
    d = tmp_path / "adir"; d.mkdir()
    lst = tmp_path / "list.txt"
    lst.write_text(f"{f1}\n{f2}\nno-such-file.txt\n{d}\n")
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    err = capsys.readouterr().err
    assert "미실존 1건" in err and "디렉터리 1건" in err
    report = _report(tmp_path)
    assert "균일 가정" in report
    assert "## 그룹 단위" not in report


# ---- REQ-006 ----------------------------------------------------------------

def test_req006_json_list(tmp_path, monkeypatch, capsys):
    items = [{"id": "OwnerController#findOwner", "w": 500, "group": "Owner",
              "note": "여분 키 관용"},
             {"id": "PetController#create", "w": 100, "group": "Pet"},
             {"id": "VetController#list", "w": 100, "group": "Vet"}]
    lst = tmp_path / "targets.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    report = _report(tmp_path)
    # 명시 w가 상대 크기로 반영 — 상위 표 최상단이 최대 w 항목
    table_start = report.index("## w 상위 10 대상")
    first_row = [ln for ln in report[table_start:].splitlines()
                 if ln.startswith("|") and "Controller#" in ln][0]
    assert "OwnerController#findOwner" in first_row
    assert "Owner" in report and "Pet" in report


def test_req006_json_missing_id(tmp_path, monkeypatch):
    lst = tmp_path / "t.json"
    lst.write_text(json.dumps([{"id": "a"}, {"w": 3}]))
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "항목 1" in str(e.value)


def test_req006_json_bad_w(tmp_path, monkeypatch):
    for bad in (0, -1, "big", True):
        lst = tmp_path / "t.json"
        lst.write_text(json.dumps([{"id": "a", "w": bad}]))
        with pytest.raises(SystemExit) as e:
            _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
        assert "항목 0" in str(e.value) and "양수" in str(e.value)


def test_req006_json_not_array(tmp_path, monkeypatch):
    lst = tmp_path / "t.json"; lst.write_text(json.dumps({"id": "a"}))
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "배열" in str(e.value)


def test_req006_json_syntax_error(tmp_path, monkeypatch):
    lst = tmp_path / "t.json"; lst.write_text("[{'broken json")
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "파싱 실패" in str(e.value)


def test_req006_json_duplicate_id(tmp_path, monkeypatch):
    lst = tmp_path / "t.json"
    lst.write_text(json.dumps([{"id": "a"}, {"id": "a"}]))
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "중복" in str(e.value)


# ---- REQ-007 ----------------------------------------------------------------

def test_req007_groups_output(tmp_path, monkeypatch, capsys):
    items = [{"id": "a1", "group": "A"}, {"id": "a2", "group": "A"},
             {"id": "b1", "group": "B"}, {"id": "b2", "group": "B"}]
    lst = tmp_path / "targets.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst), "--groups"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "그룹1(" in out and "a1" in out
    assert "[targets]" in out          # --groups 헤더 = 목록 파일 stem (design §3)
    p = _plan(tmp_path)
    # 용량 이내 fixture — 각 group의 항목 전부가 동일 청크 index
    for grp in ("a", "b"):
        idxs = {i for i, c in enumerate(p["chunks"])
                for ep in c["endpoints"] if ep.startswith(grp)}
        assert len(idxs) == 1, (grp, idxs)
    raw = (tmp_path / ".kn/kn-plan.json").read_text()
    assert "\\u0000" not in raw and "\x00" not in raw


def test_req007_mixed_group_section(tmp_path, monkeypatch, capsys):
    items = [{"id": "g1", "group": "G"}, {"id": "g2", "group": "G"},
             {"id": "x1"}, {"id": "x2"}]
    lst = tmp_path / "targets.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    report = _report(tmp_path)
    grp_rows = [ln for ln in _section(report, "## 그룹 단위").splitlines()
                if ln.startswith("| ")]
    assert any("G" in ln for ln in grp_rows)
    assert not any("x1" in ln or "x2" in ln for ln in grp_rows)
    raw = (tmp_path / ".kn/kn-plan.json").read_text()
    assert "\\u0000" not in raw and "\x00" not in raw
    assert "\x00" not in report


# ---- REQ-008 ----------------------------------------------------------------

def test_req008_both_sources(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, [str(tmp_path), "--targets", "x.txt"], cwd=tmp_path)
    assert "정확히 하나" in str(e.value)


def test_req008_no_source(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, [], cwd=tmp_path)
    assert "정확히 하나" in str(e.value)


# ---- REQ-009 ----------------------------------------------------------------

def test_req009_duplicate_path_normalized(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    lst = tmp_path / "list.txt"
    lst.write_text("a.txt\n./a.txt\n")
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "중복" in str(e.value)


def test_req009_empty_list(tmp_path, monkeypatch):
    lst = tmp_path / "list.txt"; lst.write_text("# 주석뿐\n\n")
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "유효 항목이 없다" in str(e.value)


def test_req009_n_zero(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--n", "0"], cwd=tmp_path)
    assert "양수" in str(e.value)


def test_req009_missing_file(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--targets", str(tmp_path / "no.txt")], cwd=tmp_path)
    assert "목록 파일이 없다" in str(e.value)


# ---- REQ-010 ----------------------------------------------------------------

def test_req010_outlier_warning_position(tmp_path, monkeypatch, capsys):
    items = [{"id": f"t{i}", "w": 100} for i in range(5)] + \
            [{"id": "monster", "w": 900}]
    lst = tmp_path / "list.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst), "--groups"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "이상치" in out and "별도 라벨로 분리 측정" in out
    assert out.index("N=") < out.index("이상치") < out.index("그룹1(") < out.index("ℹ")
    warn_block = out[out.index("이상치"):out.index("그룹1(")]
    assert "파일럿" not in warn_block
    report = _report(tmp_path)
    assert "이상치" in report and "monster" in report
    assert "별도 라벨로 분리 측정" in report


def test_req010_no_warning_uniform(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, ["--n", "10"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "이상치" not in out
    assert "이상치" not in _report(tmp_path)


# ---- REQ-011 ----------------------------------------------------------------

def test_req011_scanner_baseline_unchanged(monkeypatch, capsys):
    """REQ-011: 스캐너 경로 수치 기준선 불변 (SUT 부재 시 skip — CI 허용)."""
    if not SUT.exists():
        pytest.skip(f"SUT 없음 ({SUT}) — KN_SUT 환경변수로 지정 가능")
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(SUT),
                                      "--label", "template", "--model", "sonnet"])
    cli.main()
    out = capsys.readouterr().out
    assert "N=18 chunks=3 k_avg=6.0 est=$21.18" in out


# ---- REQ-012 ----------------------------------------------------------------

def test_req012_report_vocabulary(tmp_path, monkeypatch, capsys):
    lst = tmp_path / "list.txt"
    lst.write_text("alpha\nbeta\ngamma\n")
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    report = _report(tmp_path)
    assert "N = 3** 대상" in report          # "- **N = 3** 대상"
    assert "엔드포인트" not in report
    assert "미해결" not in report
    assert "## 그룹 단위" not in report
    assert "정적 슬라이스" not in report
    assert "## w 상위" not in report
    assert "균일 가정" in report
    assert (tmp_path / ".kn/kn-report.md").exists()


def test_req012_n_source_line(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, ["--n", "5"], cwd=tmp_path)
    assert "(--n 5)" in _report(tmp_path)


def test_req012_file_w_report(tmp_path, monkeypatch, capsys):
    lst = _files_list(tmp_path)
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    report = _report(tmp_path)
    assert "## w 상위 10 대상" in report
    assert "| 대상 | w (tokens) |" in report
    assert "external" not in report and "unresolved" not in report
    assert "파일 크기(bytes/4)" in report


def test_req012_json_w_report(tmp_path, monkeypatch, capsys):
    items = [{"id": "a", "w": 10}, {"id": "b", "w": 20}]
    lst = tmp_path / "t.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst)], cwd=tmp_path)
    assert "사용자 제공값" in _report(tmp_path)


def test_req012_pilot_notice_noun(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, ["--n", "3"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "ℹ" in out
    assert "대상 1개짜리" in out
    assert "EP 1개짜리" not in out


def test_req012_env_wall_noun(tmp_path, monkeypatch, capsys):
    cal = json.loads(BUNDLED_CALIBRATION.read_text())
    cell = dict(cal["cells"]["template/sonnet"])
    cell.update({"S0": 200_000, "delta_env": 110_000, "delta_ep": 2_000})
    cal["cells"] = {"template/sonnet": cell}
    cal.pop("skipped_cells", None)
    fx = tmp_path / "cal.json"; fx.write_text(json.dumps(cal))
    _run(monkeypatch, ["--n", "5", "--calibration", str(fx)], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "대상당 1청크로 퇴화" in out
    assert "EP당" not in out


# ---- REQ-013 ----------------------------------------------------------------

def test_req013_concepts_doc_linked():
    concepts = REPO / "docs/CONCEPTS.md"
    assert concepts.exists()
    assert "CONCEPTS.md" in (REPO / "README.md").read_text()
    assert "CONCEPTS.md" in (REPO / "README.en.md").read_text()
    assert "--targets" in (REPO / "docs/GUIDE.md").read_text()
    calib = (REPO / "docs/CALIBRATION.md").read_text()
    assert "--units" not in calib
    assert "미구현" not in calib


# ---- targets.py 단위 테스트 (내부 루프) --------------------------------------

def test_unit_parse_text_file_w(tmp_path, monkeypatch):
    from kn_estimator import targets
    monkeypatch.chdir(tmp_path)
    a = tmp_path / "a.txt"; a.write_text("x" * 4000)
    b = tmp_path / "b.txt"; b.write_text("y" * 400)
    z = tmp_path / "zero.txt"; z.write_text("")
    lst = tmp_path / "l.txt"; lst.write_text("a.txt\nb.txt\nzero.txt\n")
    meta = targets.parse_targets(str(lst))
    assert meta["w_source"] == "file" and meta["n"] == 3
    ws = {s["endpoint"]["path"]: s["w_tokens"] for s in meta["slices"]}
    assert ws["a.txt"] == 1000 and ws["b.txt"] == 100
    assert ws["zero.txt"] == 1        # 0바이트 클램프 (design §4.1)


def test_unit_parse_json_ok(tmp_path):
    from kn_estimator import targets
    lst = tmp_path / "l.json"
    lst.write_text(json.dumps([{"id": "a", "w": 3, "group": "G"}, {"id": "b"}]))
    meta = targets.parse_targets(str(lst))
    assert meta["w_source"] == "json"
    ctrl = {s["endpoint"]["path"]: s["endpoint"]["controller"]
            for s in meta["slices"]}
    assert ctrl["a"] == "G" and ctrl["b"].startswith("\x00")


def test_unit_outliers_below_n4():
    from kn_estimator import targets
    sls = [{"w_tokens": w} for w in (1, 1, 100)]
    assert targets.outliers(sls) == []


def test_unit_outliers_median_uniform_silent():
    from kn_estimator import targets
    sls = [{"w_tokens": 5.0} for _ in range(10)]
    assert targets.outliers(sls) == []


def test_unit_n_targets_padding():
    from kn_estimator import targets
    meta = targets.n_targets(100)
    ids = [s["endpoint"]["path"] for s in meta["slices"]]
    assert ids[0] == "unit-001" and ids[-1] == "unit-100"
    assert ids == sorted(ids)
