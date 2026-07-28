"""캘리브레이션: run_ledger.jsonl + 트랜스크립트 → calibration.json.

셀(라벨×모델)별 관측 가능량만 추출:
  S0(첫 턴 컨텍스트), tau/delta/out의 env(1회 고정분)·ep(한계분) 분해,
  latency(초/턴), 실측 run 비용 목록(분산 폭).

셀은 원장 row의 label/model 필드로 정의된다 — label은 작업 유형의 자유 이름표다
(동봉 실측은 template·flat 두 생성 전략을 라벨로 쓴다). env/ep 분해는 크기가
다른 N 2점의 1차 fit이므로, 단일 N 셀은 산출되지 않는다.
"""
import argparse, json, statistics, sys
from pathlib import Path

PRICING = {"opus": {"input": 5.0, "output": 25.0},
           "sonnet": {"input": 3.0, "output": 15.0},
           "haiku": {"input": 1.0, "output": 5.0},
           "cache_write_mult": 2.0, "cache_read_mult": 0.10}


def _turn_stats(tr_path):
    seen, ctxs = set(), []
    for l in Path(tr_path).read_text().splitlines():
        r = json.loads(l)
        if r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        if m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        u = m.get("usage") or {}
        ctxs.append(u.get("cache_read_input_tokens", 0) + u.get("input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0))
    return len(ctxs), (ctxs[0] if ctxs else 0), (max(ctxs) if ctxs else 0)


def _load_ledger(ledger_path):
    """원장 로드 — 부재·깨진 줄을 raw traceback 대신 원인·위치와 함께 알린다."""
    p = Path(ledger_path)
    if not p.exists():
        raise SystemExit(f"원장을 찾을 수 없다: {p}")
    rows = []
    for lineno, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"원장 파싱 실패: {p}:{lineno}: {e}")
    return rows


def calibrate(ledger_path, runs_dir, include=None, min_runs=2):
    rows = _load_ledger(ledger_path)
    # 게이트 통과 run만 — 실패 run은 조기 종료로 비용이 과소해 계수를 오염시킨다.
    # 사후 재판정(측정 인프라 위양성)된 run은 gate-adjudications.json으로 구제.
    # 걸러진 사유는 셀별로 집계한다 — 셀의 run이 전부 걸러지면(캠페인 실측: haiku
    # 게이트 0/6) 그 셀은 groups에 아예 없어, 집계 없이는 무플래그로 사라진다.
    adj_path = Path(ledger_path).parent / "gate-adjudications.json"
    adjudicated = set(json.loads(adj_path.read_text())["adjudicated_pass"]) if adj_path.exists() else set()
    per_run = {}
    dropped = {}   # cell key -> {"gate_fail": n, "missing_transcript": n, "usable": n}
    for r in rows:
        if (r.get("rep") == 99 or r["role"] != "run_total"
                or (include is not None and not include(r))):
            continue
        if not (r.get("label") and r.get("model")):
            raise SystemExit(
                f"원장 run {r.get('run_id', '?')}: label/model 필드가 없다 — "
                "셀은 <label>/<model>로 정의된다 (docs/CALIBRATION.md §4).")
        if "/" in r["label"]:
            raise SystemExit(
                f"원장 run {r['run_id']}: 라벨에 '/'를 쓸 수 없다 ('{r['label']}') — "
                "셀 키 구분자와 충돌한다.")
        key = f"{r['label']}/{r['model']}"
        cnt = dropped.setdefault(key, {"gate_fail": 0, "missing_transcript": 0,
                                       "usable": 0})
        if not (r.get("gate") == "pass" or r["run_id"] in adjudicated):
            cnt["gate_fail"] += 1
            continue
        tr = Path(runs_dir) / r["run_id"] / "transcript.jsonl"
        if not tr.exists():
            cnt["missing_transcript"] += 1
            continue
        cnt["usable"] += 1
        turns, s0, cmax = _turn_stats(tr)
        per_run[r["run_id"]] = {"cell": key, "n": r["n"],
                                "turns": turns, "s0": s0, "cmax": cmax,
                                "out": r["output_tokens"], "cost": r["cost_usd"],
                                "wall": r["wall_s"],
                                # 하네스 메타(계획 D2): 결측은 claude-code의 암묵 별칭
                                # (이 저장소의 기존 원장은 전부 Claude Code 실측이다)
                                "harness": r.get("harness") or "claude-code",
                                "out_approx": r.get("out_approx")}
    cells = {}
    groups = {}
    for v in per_run.values():
        groups.setdefault(v["cell"], []).append(v)

    def med(vals):
        return statistics.median(vals) if vals else None

    def two_point(runs):
        by_n = {}
        for r in runs:
            by_n.setdefault(r["n"], []).append(r)
        if len(by_n) < 2:
            return None
        (n1, g1), (n2, g2) = sorted(by_n.items())[:2]
        def fit(field, s0_adjust=False):
            v1 = med([g[field] - (g["s0"] if s0_adjust else 0) for g in g1])
            v2 = med([g[field] - (g["s0"] if s0_adjust else 0) for g in g2])
            ep = (v2 - v1) / (n2 - n1)
            env = v1 - ep * n1
            return max(env, 0), max(ep, 1)
        d_env, d_ep = fit("cmax", s0_adjust=True)
        t_env, t_ep = fit("turns")
        o_env, o_ep = fit("out")
        return d_env, d_ep, t_env, t_ep, o_env, o_ep

    # 산출에서 빠지는 셀은 사유와 함께 기록한다 — 무플래그 drop은 하류에서 원인 불명의
    # insufficient_calibration으로만 보인다 (2026-07-26 감사 #5).
    skipped = {}
    for key, runs in groups.items():
        if len(runs) < min_runs:
            m = dropped.get(key, {}).get("missing_transcript", 0)
            extra = f", missing_transcript={m}" if m else ""
            skipped[key] = f"insufficient_runs({len(runs)}<{min_runs}{extra})"
            continue
        s0 = med([r["s0"] for r in runs])
        tp = two_point(runs)
        if tp is None:
            # env/ep 분해는 크기가 다른 N 2점이 필요하다 — 단일 N 셀은 산출 불가
            skipped[key] = "single_n(크기가 다른 N 2점 필요)"
            continue
        d_env, d_ep, t_env, t_ep, o_env, o_ep = tp
        # run 분산 밴드용 실측 비용은 셀의 최대 N(전체 규모 지점)에서 취한다.
        # 구 구현은 n == 8 리터럴이었다 — 초기 캘리브레이션 SUT(최대 N=8) 전제가 새어나온 것으로,
        # 최대 N이 다른 프로젝트에서 빈 배열이 되어 밴드가 기본값으로 조용히 퇴화했다.
        max_n = max(r["n"] for r in runs)
        # out 근사 플래그 전파(계획 D4) — 계약상 전파 범위는 calibration.json까지.
        # 필드가 있는 run이 하나라도 근사면 셀도 근사로 표시한다. 필드가 전무한
        # 기존 원장에서는 키를 만들지 않아 동봉 번들이 바이트 불변으로 유지된다.
        oa = [x["out_approx"] for x in runs if x.get("out_approx") is not None]
        cells[key] = {
            "S0": s0, "delta_env": d_env, "delta_ep": d_ep,
            "tau_env": t_env, "tau_ep": t_ep, "out_env": o_env, "out_ep": o_ep,
            "latency_s_per_turn": med([r["wall"] / max(r["turns"], 1) for r in runs]),
            "measured_costs": sorted(round(r["cost"], 2) for r in runs if r["n"] == max_n),
            # env_split_approx: 과거 단일-N 근사 경로의 흔적 — 현재는 항상 2점
            # fit이므로 False 고정 (동봉 파일 스키마 안정성 위해 필드 유지).
            "n_runs": len(runs), "env_split_approx": False,
        }
        if oa:
            cells[key]["out_approx"] = any(oa)
    # 하네스 혼합 검출(계획 D2): 같은 셀에 서로 다른 하네스의 run이 섞이면 계수가
    # 오염된다 (계수는 하네스의 함수 — S0·τ·out_env가 하네스에 지배됨).
    harness_mixed = {}
    for key, runs in groups.items():
        hs = sorted({x["harness"] for x in runs})
        if len(hs) > 1:
            harness_mixed[key] = hs

    # 사용 가능 run이 0인 셀(게이트 전멸·트랜스크립트 전멸)은 groups에 없다 — 여기서 기록.
    # 원장에 등장하지 않는 셀은 기록하지 않는다 (의도된 미실험인지 알 수 없다).
    for key, cnt in dropped.items():
        if key in cells or key in skipped or cnt["usable"] > 0:
            continue
        parts = [f"{k}={v}" for k, v in cnt.items() if k != "usable" and v]
        skipped[key] = f"no_usable_runs({', '.join(parts)})"
    out = {"version": "kn-cal-1", "source": str(ledger_path), "pricing": PRICING,
           "alpha_default": 0.5, "cells": cells, "skipped_cells": skipped}
    if harness_mixed:   # 비어 있으면 키를 만들지 않는다 — 동봉 번들 바이트 불변 유지
        out["harness_mixed"] = harness_mixed
    return out


def main(argv=None):
    """원장에서 calibration.json을 재생성한다 — 경로는 인자로 받는다.

    저장소 구조를 추정(구 `parents[2]`)하지 않는다. 그 추정은 디렉토리가 바뀌면 조용히
    깨졌고, 패키지 설치본에서는 애초에 성립하지 않는다.
    """
    ap = argparse.ArgumentParser(description="실측 원장에서 kn-estimator 캘리브레이션 산출")
    ap.add_argument("--ledger", type=Path, required=True, help="run_ledger.jsonl 경로")
    ap.add_argument("--runs", type=Path, required=True, help="runs/ 디렉토리 (run_id/transcript.jsonl)")
    ap.add_argument("--out", type=Path, help="출력 경로 (생략 시 stdout)")
    args = ap.parse_args(argv)
    cal = calibrate(args.ledger, args.runs)
    if not cal["cells"]:
        # 조용한 실패 금지 — cells가 비면 파이프라인이 성공으로 오인하고, 그 파일을
        # 물린 kn-estimate만 뒤늦게 죽는다.
        raise SystemExit(
            "사용 가능한 run이 0건 — 셀이 산출되지 않았다 "
            f"(제외 사유: {cal['skipped_cells'] or '기록 없음 — 원장에 매칭 run 자체가 없음'}). "
            "원장의 label/model/gate/n 필드와 --runs 트랜스크립트 경로를 확인하라 (docs/GUIDE.md §4.4).")
    for cell, why in cal["skipped_cells"].items():
        print(f"경고: 셀 {cell} 제외 — {why}", file=sys.stderr)
    for cell, hs in cal.get("harness_mixed", {}).items():
        print(f"경고: 셀 {cell}에 서로 다른 harness가 섞였다: {', '.join(hs)} — "
              "계수는 하네스의 함수라 혼합은 계수를 오염시킨다. 하네스별로 원장을 "
              "분리해 캘리브레이션하라.", file=sys.stderr)
    text = json.dumps(cal, indent=1, ensure_ascii=False)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out} ({len(cal['cells'])} cells)")
    else:
        print(text)


if __name__ == "__main__":
    main()
