#!/usr/bin/env python3
"""kiro2kn — Kiro CLI 세션을 kn-calibrate 입력(트랜스크립트+원장 라인)으로 변환.

계획: docs/superpowers/plans/2026-07-28-kiro2kn-adapter-plan.md
사용법: docs/CALIBRATION.md §5. stdlib만 사용한다.

  python research/adapters/kiro2kn.py --list [--cwd <경로>] [--db <sqlite>]
  # 가장 흔한 경로 — 파일럿 세션 종료 직후, 그 프로젝트 디렉토리에서 한 줄
  # (스크립트는 kn-estimator 체크아웃의 절대경로로 지정):
  python <kn-estimator>/research/adapters/kiro2kn.py --latest \\
      --mode template --model sonnet --n 1 --gate pass [--cost 0.42]
  # 또는 kn-estimator 저장소 안에서 --cwd로 파일럿 디렉토리를 지정:
  python research/adapters/kiro2kn.py --latest --cwd ~/work/my-backend \\
      --mode template --model sonnet --n 1 --gate pass
  # 명시 선택 경로:
  python research/adapters/kiro2kn.py <conversation_id 접두> \\
      --mode template --model sonnet --n 1 --rep 1 --gate pass [--cost 0.42] \\
      [--run-id <id>] [--runs-dir runs/] [--ledger run_ledger.jsonl] [--db <sqlite>]
  python research/adapters/kiro2kn.py --sessions-jsonl <이벤트로그> ...  # 원장 전용 폴백

주의: 계수는 하네스의 함수다 — Kiro run은 Kiro끼리만 캘리브레이션하라
(원장 라인의 harness 필드가 혼합을 경고한다).
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path.home() / "Library/Application Support/kiro-cli/data.sqlite3"
HARNESS = "kiro-cli"


def _connect(db):
    p = Path(db)
    if not p.exists():
        raise SystemExit(f"Kiro sqlite를 찾을 수 없다: {p} (--db 로 지정 가능)")
    try:
        # 읽기 전용 — Kiro CLI가 세션 중 쓰기 락을 쥘 수 있다. 변환은 세션 종료 후에.
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM conversations_v2 LIMIT 1")
        return con
    except sqlite3.OperationalError as e:
        raise SystemExit(f"sqlite 열기 실패: {p}: {e} — Kiro 세션이 진행 중이면 "
                         "종료 후 다시 시도하라.")


def _require(obj, key, where):
    if isinstance(obj, dict) and key in obj and obj[key] is not None:
        return obj[key]
    raise SystemExit(f"Kiro 스키마 불일치: {where}에 '{key}'가 없다 — "
                     "Kiro 버전이 계획서 §0의 기준과 다를 수 있다.")


def _first_prompt(value):
    try:
        return value["history"][0]["user"]["content"]["Prompt"]["prompt"]
    except (KeyError, IndexError, TypeError):
        return ""


def list_conversations(con, cwd=None):
    q = ("SELECT key, conversation_id, value, created_at, updated_at "
         "FROM conversations_v2 ")
    args = ()
    if cwd:
        q += "WHERE key = ? "
        args = (cwd,)
    q += "ORDER BY updated_at DESC"
    for key, cid, raw, created, updated in con.execute(q, args):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t0 = datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d %H:%M")
        t1 = datetime.fromtimestamp(updated / 1000).strftime("%H:%M")
        preview = _first_prompt(value)[:40]
        print(f"  {cid[:8]}  {t0}→{t1}  {len(value.get('history', []))}턴  \"{preview}…\"")


def latest(con, cwd):
    row = con.execute(
        "SELECT conversation_id FROM conversations_v2 WHERE key = ? "
        "ORDER BY updated_at DESC LIMIT 1", (cwd,)).fetchone()
    if not row:
        raise SystemExit(f"'{cwd}'에서 실행된 Kiro 대화가 없다 — --cwd로 세션의 "
                         "작업 디렉토리를 지정하거나 --list로 확인하라.")
    return row[0]


def resolve(con, prefix):
    rows = con.execute(
        "SELECT conversation_id FROM conversations_v2 "
        "WHERE conversation_id LIKE ? ORDER BY updated_at DESC",
        (prefix + "%",)).fetchall()
    if not rows:
        raise SystemExit(f"'{prefix}'로 시작하는 대화가 없다 — --list로 확인하라.")
    if len(rows) > 1:
        cands = ", ".join(r[0][:13] for r in rows)
        raise SystemExit(f"'{prefix}'가 여러 대화와 충돌한다: {cands} — "
                         "더 긴 접두를 지정하라.")
    return rows[0][0]


def extract(value):
    """(턴별 usage 목록, output_tokens 합, out_approx) — D3 2단 폴백 + D4."""
    history = _require(value, "history", "value")
    if not history:
        # 0턴 transcript는 kn-calibrate에서 usable로 집계돼 tau_env·out_env를
        # 0으로 붕괴시킨다 — 중단 세션은 시끄럽게 거부한다.
        raise SystemExit("history가 비어 있다 — 중단된 세션이거나 잘못된 "
                         "conversation_id다. --list로 턴 수를 확인하라.")
    window = _require(_require(value, "model_info", "value"),
                      "context_window_tokens", "model_info")
    usages, out_total, approx_out = [], 0, 0
    all_real = bool(history)
    for i, h in enumerate(history):
        rm = _require(h, "request_metadata", f"history[{i}]")
        if rm.get("total_tokens") is not None:
            usages.append({"cache_read_input_tokens": rm.get("cache_read_input_tokens") or 0,
                           "input_tokens": rm.get("uncached_input_tokens") or 0,
                           "cache_creation_input_tokens": rm.get("cache_write_input_tokens") or 0})
            out_total += rm.get("output_tokens") or 0
        else:
            all_real = False
            pct = _require(rm, "context_usage_percentage", f"history[{i}].request_metadata")
            usages.append({"cache_read_input_tokens": round(pct / 100 * window),
                           "input_tokens": 0, "cache_creation_input_tokens": 0})
        # out 근사 재료는 항상 집계해 두고, 전 턴이 실측이면 실측 합을 쓴다
        approx_out += len(json.dumps(h.get("assistant") or {},
                                     ensure_ascii=False).encode()) // 4
    if not all_real:
        out_total = approx_out
    return usages, out_total, (not all_real)


def write_transcript(runs_dir, run_id, cid, usages):
    d = Path(runs_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "transcript.jsonl", "w") as f:
        for i, u in enumerate(usages):
            # message.id는 턴 인덱스로 고유하게 합성한다 — _turn_stats가 id로
            # dedup하므로 고유하지 않으면 τ가 붕괴한다 (계획 D3).
            f.write(json.dumps({"type": "assistant",
                                "message": {"id": f"{cid}-{i}", "usage": u}}) + "\n")
    return d / "transcript.jsonl"


def append_ledger(ledger, row):
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def from_sessions_jsonl(path):
    """원장 전용 폴백(D8): AssistantMessage 바이트로 out 근사, timestamp 차로 wall_s.
    request_metadata가 없어 컨텍스트 복원이 불가하므로 트랜스크립트는 만들지 않는다."""
    out_bytes, stamps = 0, []
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"세션 이벤트 로그를 찾을 수 없다: {p}")
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"이벤트 로그 파싱 실패: {p}: {e}")
        data = ev.get("data") or {}
        if ev.get("kind") == "AssistantMessage":
            out_bytes += len(json.dumps(data.get("content") or [],
                                        ensure_ascii=False).encode())
        ts = (data.get("meta") or {}).get("timestamp")
        if ts is not None:
            stamps.append(ts)
    wall_s = round(max(stamps) - min(stamps)) if len(stamps) >= 2 else 0
    return out_bytes // 4, wall_s


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Kiro CLI 세션 → kn-calibrate 입력 변환 (docs/CALIBRATION.md §5)")
    ap.add_argument("conversation", nargs="?",
                    help="conversation_id 접두 (모호하면 후보를 나열하고 실패)")
    ap.add_argument("--list", action="store_true", help="대화 목록 (최신순)")
    ap.add_argument("--latest", action="store_true",
                    help="현재 디렉토리(또는 --cwd)의 가장 최근 대화를 자동 선택")
    ap.add_argument("--cwd", help="--list 필터 / --latest 대상 작업 디렉토리 "
                                  "(기본: 현재 디렉토리)")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"Kiro sqlite 경로 (기본 {DEFAULT_DB})")
    ap.add_argument("--sessions-jsonl",
                    help="원장 전용 폴백 — sqlite에서 정리된 대화의 이벤트 로그")
    ap.add_argument("--label",
                    help="작업 라벨 — kn-estimate와 같은 어휘 (--model과 함께 지정)")
    ap.add_argument("--model", choices=["sonnet", "opus", "haiku"],
                    help="대상 모델 (--label과 함께 지정)")
    ap.add_argument("--n", type=int, help="이 run이 처리한 엔드포인트 수")
    ap.add_argument("--rep", type=int, default=1, help="반복 번호")
    ap.add_argument("--gate", choices=["pass", "fail"],
                    help="게이트 판정 (사용자 책임 — 컴파일 기준 권장)")
    ap.add_argument("--cost", type=float,
                    help="이 run의 비용(USD 환산). 미지정 시 0 + 경고")
    ap.add_argument("--run-id", help="원장 run_id (기본: <cwd이름>_<variant>-n<n>-r<rep>)")
    ap.add_argument("--runs-dir", default="runs", help="트랜스크립트 디렉토리 (기본 runs/)")
    ap.add_argument("--ledger", default="run_ledger.jsonl",
                    help="원장 파일 — 한 줄 append (기본 run_ledger.jsonl)")
    args = ap.parse_args(argv)

    if args.list:
        con = _connect(args.db)
        list_conversations(con, args.cwd)
        return

    if args.latest and args.conversation:
        raise SystemExit("--latest와 conversation_id 접두는 동시에 쓸 수 없다 — "
                         "다른 세션이 조용히 선택되는 것을 막기 위해서다.")
    if args.latest and args.sessions_jsonl:
        raise SystemExit("--latest와 --sessions-jsonl은 동시에 쓸 수 없다.")
    if not (args.label and args.model and args.n and args.gate):
        raise SystemExit("--label/--model, --n, --gate 는 변환에 필수다.")
    cost = args.cost
    if cost is None:
        print("경고: --cost 미지정 — cost_usd=0으로 기록한다 (상대 비교 전용). "
              "Kiro 크레딧의 USD 환산액을 아는 경우 --cost로 지정하라.",
              file=sys.stderr)
        cost = 0.0

    if args.sessions_jsonl:
        out_tokens, wall_s = from_sessions_jsonl(args.sessions_jsonl)
        run_id = args.run_id or f"kiro_{args.label}-{args.model}-n{args.n}-r{args.rep}"
        row = {"run_id": run_id, "label": args.label, "model": args.model,
               "role": "run_total",
               "n": args.n, "rep": args.rep, "gate": args.gate,
               "cost_usd": cost, "output_tokens": out_tokens, "wall_s": wall_s,
               "harness": HARNESS, "out_approx": True}
        append_ledger(args.ledger, row)
        print(f"{args.ledger}에 1줄 추가 (트랜스크립트 없음 — 이 run은 "
              "kn-calibrate에서 missing_transcript로 계수에서 제외된다)")
        return

    con = _connect(args.db)
    if args.latest:
        cid = latest(con, args.cwd or os.getcwd())
    elif args.conversation:
        cid = resolve(con, args.conversation)
    else:
        raise SystemExit("conversation_id 접두, --latest, 또는 --list 중 하나를 쓰라.")
    cwd, raw, created, updated = con.execute(
        "SELECT key, value, created_at, updated_at FROM conversations_v2 "
        "WHERE conversation_id = ?", (cid,)).fetchone()
    value = json.loads(raw)
    usages, out_tokens, out_approx = extract(value)
    wall_s = round((updated - created) / 1000)   # D7 — 세션 벽시계
    run_id = args.run_id or f"{Path(cwd).name}_{args.label}-{args.model}-n{args.n}-r{args.rep}"
    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)   # 반쪽 산출물 방지
    tr = write_transcript(args.runs_dir, run_id, cid, usages)
    row = {"run_id": run_id, "label": args.label, "model": args.model,
           "role": "run_total",
           "n": args.n, "rep": args.rep, "gate": args.gate,
           "cost_usd": cost, "output_tokens": out_tokens, "wall_s": wall_s,
           "harness": HARNESS, "out_approx": out_approx}
    append_ledger(args.ledger, row)
    print(f"{tr} 생성 ({len(usages)}턴)")
    print(f"{args.ledger}에 1줄 추가: run_id={run_id} out_approx={out_approx} "
          f"wall_s={wall_s}")


if __name__ == "__main__":
    main()
