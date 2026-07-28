"""kiro2kn 어댑터 수용 테스트 — 계획 REQ-001~011.

계획: docs/superpowers/plans/2026-07-28-kiro2kn-adapter-plan.md
합성 sqlite 픽스처는 실측 스키마(conversations_v2, history[i] =
{user, assistant, request_metadata})를 그대로 재현한다.
"""
import importlib.util
import json
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "kiro2kn", REPO / "research/adapters/kiro2kn.py")
kiro2kn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kiro2kn)

from kn_estimator import calibrate  # noqa: E402


# ---- 합성 sqlite 픽스처 (실측 스키마 재현) ------------------------------------

WINDOW = 1_000_000


def _turn(pct=None, tokens=None, prompt=None, assistant_bytes=400):
    rm = {"context_usage_percentage": pct, "model_id": "auto",
          "total_tokens": None, "uncached_input_tokens": None,
          "output_tokens": None, "cache_read_input_tokens": None,
          "cache_write_input_tokens": None}
    if tokens:
        rm.update(tokens)
    user = {"content": {"Prompt": {"prompt": prompt or "계속"}},
            "timestamp": "2026-07-28T14:00:00Z"}
    assistant = {"Response": {"message_id": "m", "content": "x" * assistant_bytes}}
    return {"user": user, "assistant": assistant, "request_metadata": rm}


def _conversation(cid, cwd, turns, created=1_784_000_000_000, wall_ms=120_000,
                  window=WINDOW, drop_keys=()):
    value = {"conversation_id": cid, "history": turns,
             "model_info": {"model_name": "auto", "context_window_tokens": window}}
    for k in drop_keys:
        value.pop(k, None)
    return (cwd, cid, json.dumps(value), created, created + wall_ms)


def _make_db(tmp_path, rows):
    db = tmp_path / "data.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations_v2 "
                "(key TEXT, conversation_id TEXT, value TEXT, "
                "created_at INT, updated_at INT)")
    con.executemany("INSERT INTO conversations_v2 VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


def _pct_turns(n, start_pct=1.0, step_pct=0.5, prompt=None):
    return [_turn(pct=start_pct + i * step_pct,
                  prompt=prompt if i == 0 else None) for i in range(n)]


def _run_cli(*argv):
    return kiro2kn.main(list(argv))


# ---- REQ-001a/b: 목록 ---------------------------------------------------------

def _list_db(tmp_path):
    return _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/work/backend",
                      _pct_turns(3, prompt="백엔드 테스트 생성"), created=2_000_000_000_000),
        _conversation("bbbb2222-0000-0000-0000-000000000002", "/work/other",
                      _pct_turns(2, prompt="다른 작업"), created=1_000_000_000_000),
    ])


def test_req001a_list_filters_by_cwd(tmp_path, capsys):
    db = _list_db(tmp_path)
    _run_cli("--db", str(db), "--list", "--cwd", "/work/backend")
    out = capsys.readouterr().out
    assert "aaaa1111" in out and "bbbb2222" not in out
    assert "3턴" in out and "백엔드 테스트 생성" in out


def test_req001b_list_without_cwd_shows_all(tmp_path, capsys):
    db = _list_db(tmp_path)
    _run_cli("--db", str(db), "--list")
    out = capsys.readouterr().out
    assert "aaaa1111" in out and "bbbb2222" in out
    # 최신순: aaaa(2e12)가 bbbb(1e12)보다 먼저
    assert out.index("aaaa1111") < out.index("bbbb2222")


# ---- REQ-002: pct-폴백 왕복 ---------------------------------------------------

def test_req002_convert_pct_fallback_roundtrip(tmp_path):
    cid = "cccc3333-0000-0000-0000-000000000003"
    db = _make_db(tmp_path, [_conversation(cid, "/w", _pct_turns(5), wall_ms=90_000)])
    _run_cli("--db", str(db), "cccc3333", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1.0",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    run_dir = next((tmp_path / "runs").iterdir())
    turns, s0, cmax = calibrate._turn_stats(run_dir / "transcript.jsonl")
    assert turns == 5                       # τ == len(history) — id 합성 검증
    assert s0 == round(1.0 / 100 * WINDOW)  # pct×window 복원
    assert cmax == round(3.0 / 100 * WINDOW)


# ---- REQ-003: 실토큰 필드 우선 ------------------------------------------------

def test_req003_convert_prefers_real_token_fields(tmp_path):
    cid = "dddd4444-0000-0000-0000-000000000004"
    real = [_turn(pct=50.0, tokens={"total_tokens": 1000 + i,
                                    "cache_read_input_tokens": 900 + i,
                                    "uncached_input_tokens": 100,
                                    "cache_write_input_tokens": 0,
                                    "output_tokens": 200}) for i in range(3)]
    db = _make_db(tmp_path, [_conversation(cid, "/w", real)])
    _run_cli("--db", str(db), "dddd4444", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1.0",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    turns, s0, cmax = calibrate._turn_stats(
        next((tmp_path / "runs").iterdir()) / "transcript.jsonl")
    assert (turns, s0, cmax) == (3, 1000, 1002)   # pct(50%=500K)가 아니라 실토큰
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["out_approx"] is False
    assert row["output_tokens"] == 600            # 실측 output_tokens 합


# ---- REQ-004: 원장 라인 필드 --------------------------------------------------

def test_req004_ledger_line_fields(tmp_path):
    cid = "eeee5555-0000-0000-0000-000000000005"
    db = _make_db(tmp_path, [_conversation(cid, "/w/myback", _pct_turns(4),
                                           wall_ms=95_500)])
    _run_cli("--db", str(db), "eeee5555", "--variant", "flat_template_sonnet",
             "--n", "2", "--rep", "1", "--gate", "pass", "--cost", "0.5",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["harness"] == "kiro-cli"
    assert row["out_approx"] is True
    assert row["wall_s"] == 96          # round(95,500ms/1000)
    assert row["role"] == "run_total" and row["gate"] == "pass"
    assert row["run_id"] == "myback_flat_template_sonnet-n2-r1"
    assert row["output_tokens"] > 0     # assistant 블롭 바이트/4 근사


# ---- REQ-005: harness 혼합 경고 -----------------------------------------------

def _harness_ledger(tmp_path, harnesses):
    """같은 셀(template/sonnet)에 n1/n5 run들 — harness 값만 달리한다."""
    rows, runs_dir = [], tmp_path / "runs"
    for i, h in enumerate(harnesses, 1):
        for n in (1, 5):
            rid = f"h{i}-n{n}-r{i}"
            d = runs_dir / rid
            d.mkdir(parents=True)
            recs = [{"type": "assistant",
                     "message": {"id": f"{rid}-m{t}",
                                 "usage": {"cache_read_input_tokens": 50000 + t * 2000,
                                           "input_tokens": 0,
                                           "cache_creation_input_tokens": 500}}}
                    for t in range(8)]
            (d / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
            row = {"run_id": rid, "variant": "flat_template_sonnet",
                   "role": "run_total", "n": n, "rep": i, "gate": "pass",
                   "wall_s": 600, "cost_usd": 5.0, "output_tokens": 1000}
            if h is not None:
                row["harness"] = h
            rows.append(row)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in rows))
    return ledger, runs_dir


@pytest.mark.parametrize("harnesses,expect_warn", [
    (["kiro-cli", "claude-code"], True),   # (a) 명시 혼합
    (["kiro-cli", None], True),            # (b) 결측(=claude-code 별칭)과 혼합
    (["claude-code", None], False),        # (c) 결측+명시 claude-code → 동일 취급
])
def test_req005_calibrate_mixed_harness_warnings(tmp_path, harnesses, expect_warn, capsys):
    ledger, runs = _harness_ledger(tmp_path, harnesses)
    calibrate.main(["--ledger", str(ledger), "--runs", str(runs)])
    err = capsys.readouterr().err
    assert ("서로 다른 harness" in err) is expect_warn, err


# ---- REQ-006: 스키마 불일치 시끄러운 실패 --------------------------------------

def test_req006_schema_mismatch_fails_loudly(tmp_path):
    cid = "ffff6666-0000-0000-0000-000000000006"
    db = _make_db(tmp_path, [_conversation(cid, "/w", _pct_turns(2),
                                           drop_keys=("model_info",))])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "ffff6666", "--variant", "flat_template_sonnet",
                 "--n", "1", "--rep", "1", "--gate", "pass",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "ledger.jsonl"))
    assert "model_info" in str(e.value)


# ---- REQ-007: E2E (outer loop) ------------------------------------------------

def test_req007_kiro_pilot_loop_end_to_end(tmp_path, monkeypatch, capsys):
    """합성 sqlite의 크기가 다른 run 2개 → 변환 → kn-calibrate 셀 산출(out_approx
    포함) → kn-estimate --calibration 완주."""
    db = _make_db(tmp_path, [
        _conversation("11117777-0000-0000-0000-000000000007", "/w/p",
                      _pct_turns(5, 1.0, 0.5), wall_ms=60_000),
        _conversation("22228888-0000-0000-0000-000000000008", "/w/p",
                      _pct_turns(11, 1.0, 1.0), wall_ms=180_000),
    ])
    for cid8, n, rep in (("11117777", 1, 1), ("22228888", 3, 1)):
        _run_cli("--db", str(db), cid8, "--variant", "flat_template_sonnet",
                 "--n", str(n), "--rep", str(rep), "--gate", "pass",
                 "--cost", "2.0",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "ledger.jsonl"))
    out = tmp_path / "kiro-cal.json"
    calibrate.main(["--ledger", str(tmp_path / "ledger.jsonl"),
                    "--runs", str(tmp_path / "runs"), "--out", str(out)])
    cal = json.loads(out.read_text())
    cell = cal["cells"]["template/sonnet"]
    assert cell["out_approx"] is True

    proj = tmp_path / "proj/src/main/java/com/x/PingController.java"
    proj.parent.mkdir(parents=True)
    proj.write_text(textwrap.dedent("""\
        package com.x;

        @RestController
        public class PingController {
            @GetMapping("/ping")
            public ResponseEntity<String> ping() { return ResponseEntity.ok("pong"); }
        }
    """))
    from kn_estimator import cli
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", str(tmp_path / "proj"),
                         "--calibration", str(out)])
    cli.main()
    assert "N=1" in capsys.readouterr().out


# ---- REQ-008: 접두 충돌 -------------------------------------------------------

def test_req008_ambiguous_prefix_fails_with_candidates(tmp_path):
    db = _make_db(tmp_path, [
        _conversation("abab0000-0000-0000-0000-000000000001", "/w", _pct_turns(2)),
        _conversation("abab0000-1111-0000-0000-000000000002", "/w", _pct_turns(2)),
    ])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "abab", "--variant", "flat_template_sonnet",
                 "--n", "1", "--rep", "1", "--gate", "pass",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "l.jsonl"))
    msg = str(e.value)
    assert "abab0000-0000" in msg and "abab0000-1111" in msg


# ---- REQ-009: cost 기본값 경고 ------------------------------------------------

def test_req009_cost_default_zero_warns(tmp_path, capsys):
    cid = "cafe9999-0000-0000-0000-000000000009"
    db = _make_db(tmp_path, [_conversation(cid, "/w", _pct_turns(2))])
    _run_cli("--db", str(db), "cafe9999", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    err = capsys.readouterr().err
    assert "cost" in err.lower() or "비용" in err
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["cost_usd"] == 0


# ---- REQ-010: run-id 오버라이드 -----------------------------------------------

def test_req010_run_id_override(tmp_path):
    cid = "beef0000-0000-0000-0000-00000000000a"
    db = _make_db(tmp_path, [_conversation(cid, "/w", _pct_turns(2))])
    _run_cli("--db", str(db), "beef0000", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1",
             "--run-id", "custom-run-7",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    assert (tmp_path / "runs/custom-run-7/transcript.jsonl").exists()
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["run_id"] == "custom-run-7"


# ---- REQ-011: sessions-jsonl 폴백은 원장 전용 ----------------------------------

def test_req011_sessions_jsonl_fallback_ledger_only(tmp_path):
    ev = tmp_path / "session.jsonl"
    lines = [
        {"version": "v1", "kind": "Prompt",
         "data": {"message_id": "p1", "content": [{"kind": "text", "data": "시작"}],
                  "meta": {"timestamp": 1_784_000_000}}},
        {"version": "v1", "kind": "AssistantMessage",
         "data": {"message_id": "a1",
                  "content": [{"kind": "text", "data": "응답" * 200}]}},
        {"version": "v1", "kind": "Prompt",
         "data": {"message_id": "p2", "content": [{"kind": "text", "data": "끝"}],
                  "meta": {"timestamp": 1_784_000_090}}},
    ]
    ev.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
    _run_cli("--sessions-jsonl", str(ev), "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1",
             "--run-id", "fallback-r1",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    assert not (tmp_path / "runs/fallback-r1").exists()   # 트랜스크립트 미생성
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["out_approx"] is True and row["output_tokens"] > 0
    assert row["wall_s"] == 90
    # kn-calibrate: 트랜스크립트 부재 → 기존 missing_transcript 경로로 제외
    cal = calibrate.calibrate(tmp_path / "ledger.jsonl", tmp_path / "runs")
    assert "missing_transcript" in str(cal["skipped_cells"])


# ---- 품질 리뷰 지적 고정 -------------------------------------------------------

def test_empty_history_conversation_is_rejected(tmp_path):
    """중단된 세션(history=[])을 변환하면 0턴 transcript가 usable run으로 집계돼
    tau_env·out_env를 0으로 붕괴시킨다 (리뷰 수치 재현) — 시끄럽게 거부해야 한다."""
    cid = "dead0000-0000-0000-0000-00000000000d"
    db = _make_db(tmp_path, [_conversation(cid, "/w", [])])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "dead0000", "--variant", "flat_template_sonnet",
                 "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "ledger.jsonl"))
    assert "history" in str(e.value)
    assert not (tmp_path / "runs").exists()      # 반쪽 산출물 금지
    assert not (tmp_path / "ledger.jsonl").exists()


def test_ledger_parent_dir_is_created(tmp_path):
    """첫 사용자가 아직 없는 디렉토리를 --ledger로 지정해도 traceback 없이
    동작해야 한다 (부모 디렉토리 생성)."""
    cid = "feed0000-0000-0000-0000-00000000000e"
    db = _make_db(tmp_path, [_conversation(cid, "/w", _pct_turns(2))])
    ledger = tmp_path / "calib/nested/ledger.jsonl"
    _run_cli("--db", str(db), "feed0000", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1",
             "--runs-dir", str(tmp_path / "runs"), "--ledger", str(ledger))
    assert ledger.exists()


def test_mixed_real_and_pct_turns(tmp_path):
    """혼합 대화(실토큰 턴 + pct 턴): 컨텍스트는 턴별로 각자 소스를 쓰고,
    out은 통째로 바이트 근사(out_approx=True) — Kiro가 토큰 필드를 채우기
    시작하는 전환기에 실데이터로 나타나는 상태다."""
    cid = "aaaa9999-0000-0000-0000-00000000000f"
    turns = [_turn(pct=50.0, tokens={"total_tokens": 1000,
                                     "cache_read_input_tokens": 900,
                                     "uncached_input_tokens": 100,
                                     "cache_write_input_tokens": 0,
                                     "output_tokens": 200}, assistant_bytes=400),
             _turn(pct=2.0, assistant_bytes=400)]
    db = _make_db(tmp_path, [_conversation(cid, "/w", turns)])
    _run_cli("--db", str(db), "aaaa9999", "--variant", "flat_template_sonnet",
             "--n", "1", "--rep", "1", "--gate", "pass", "--cost", "1",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    tr = next((tmp_path / "runs").iterdir()) / "transcript.jsonl"
    lines = [json.loads(l)["message"]["usage"] for l in tr.read_text().splitlines()]
    ctx = [sum(u.values()) for u in lines]
    assert ctx[0] == 1000                          # 실측 턴은 실토큰
    assert ctx[1] == round(2.0 / 100 * WINDOW)     # pct 턴은 pct×window
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["out_approx"] is True
    # out은 전 턴 바이트 근사 합 (실측 200 합산이 아님)
    assert row["output_tokens"] > 150 and row["output_tokens"] != 200


# ---- UX 간소화: --latest + --mode/--model (2026-07-28 후속) --------------------

def test_latest_picks_newest_conversation_for_cwd(tmp_path):
    """--latest: --cwd의 가장 최근 대화를 자동 선택 — 목록에서 id를 옮겨 적는
    단계를 없앤다."""
    db = _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/work/backend",
                      _pct_turns(3), created=1_000_000_000_000),
        _conversation("bbbb2222-0000-0000-0000-000000000002", "/work/backend",
                      _pct_turns(4), created=2_000_000_000_000),   # 더 최근
        _conversation("cccc3333-0000-0000-0000-000000000003", "/work/other",
                      _pct_turns(2), created=3_000_000_000_000),   # 다른 cwd
    ])
    _run_cli("--db", str(db), "--latest", "--cwd", "/work/backend",
             "--mode", "template", "--model", "sonnet",
             "--n", "1", "--gate", "pass", "--cost", "1",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["variant"] == "flat_template_sonnet"   # --mode/--model → variant 조립
    tr = tmp_path / "runs" / row["run_id"] / "transcript.jsonl"
    assert len(tr.read_text().splitlines()) == 4      # bbbb(4턴)가 선택됨


def test_latest_without_match_fails_with_guidance(tmp_path):
    db = _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/work/other",
                      _pct_turns(2))])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "--latest", "--cwd", "/work/backend",
                 "--mode", "template", "--model", "sonnet",
                 "--n", "1", "--gate", "pass", "--cost", "1",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "l.jsonl"))
    assert "--list" in str(e.value)


def test_mode_model_and_variant_are_mutually_exclusive(tmp_path):
    db = _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/w", _pct_turns(2))])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "aaaa1111", "--variant", "flat_sonnet",
                 "--mode", "template", "--model", "sonnet",
                 "--n", "1", "--gate", "pass", "--cost", "1",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "l.jsonl"))
    assert "variant" in str(e.value) and "mode" in str(e.value)


def test_latest_defaults_to_process_cwd(tmp_path, monkeypatch):
    """--cwd 미지정 시 프로세스 현재 디렉토리가 기준 — --latest의 존재 이유인
    기본 경로 분기를 직접 검증한다."""
    proj = tmp_path / "projdir"
    proj.mkdir()
    import os
    monkeypatch.chdir(proj)
    key = os.getcwd()   # macOS /tmp 심링크 해석까지 실제 값 그대로
    db = _make_db(tmp_path, [
        _conversation("abcd0000-0000-0000-0000-000000000001", key, _pct_turns(3))])
    _run_cli("--db", str(db), "--latest", "--mode", "template", "--model", "sonnet",
             "--n", "1", "--gate", "pass", "--cost", "1",
             "--runs-dir", str(tmp_path / "runs"),
             "--ledger", str(tmp_path / "ledger.jsonl"))
    assert (tmp_path / "ledger.jsonl").exists()


def test_latest_conflicts_with_explicit_prefix(tmp_path):
    """--latest와 접두 동시 지정은 조용한 무시가 아니라 오류다 — 다른 세션이
    맞는 run_id로 기록되는 무증상 오염을 막는다."""
    db = _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/w", _pct_turns(2))])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "aaaa1111", "--latest",
                 "--mode", "template", "--model", "sonnet",
                 "--n", "1", "--gate", "pass", "--cost", "1",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "l.jsonl"))
    assert "--latest" in str(e.value)


def test_mode_without_model_fails(tmp_path):
    db = _make_db(tmp_path, [
        _conversation("aaaa1111-0000-0000-0000-000000000001", "/w", _pct_turns(2))])
    with pytest.raises(SystemExit) as e:
        _run_cli("--db", str(db), "aaaa1111", "--mode", "template",
                 "--n", "1", "--gate", "pass", "--cost", "1",
                 "--runs-dir", str(tmp_path / "runs"),
                 "--ledger", str(tmp_path / "l.jsonl"))
    assert "--model" in str(e.value)
