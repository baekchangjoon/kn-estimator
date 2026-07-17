"""캘리브레이션: run_ledger.jsonl + 트랜스크립트 → calibration.json.

셀(모드×모델)별 관측 가능량만 추출 (설계 v2.1):
  S0(첫 턴 컨텍스트), tau/delta/out의 env(1회 고정분)·ep(한계분) 분해,
  latency(초/턴), out_rate(출력토큰/초), 실측 run 비용 목록(분산 폭).

env/ep 분해는 N이 2종 이상인 셀에서 2점 fit으로, 단일 N 셀은 flat/opus의
env:ep 비율을 차용(approx 플래그). 가격표·캐시 배수는 버전과 함께 동봉.
"""
import argparse, json, statistics
from pathlib import Path

ARM_TO_CELL = {"flat": ("flat", "opus"), "flat_sonnet": ("flat", "sonnet"),
               "flat_haiku": ("flat", "haiku"),
               "flat_template": ("template", "opus"),
               "flat_template_sonnet": ("template", "sonnet"),
               "flat_template_haiku": ("template", "haiku")}

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


def calibrate(ledger_path, runs_dir, include=None, min_runs=2):
    rows = [json.loads(l) for l in Path(ledger_path).read_text().splitlines()]
    # 게이트 통과 run만 — 실패 run은 조기 종료로 비용이 과소해 계수를 오염시킨다.
    # 사후 재판정(측정 인프라 위양성)된 run은 gate-adjudications.json으로 구제.
    adj_path = Path(ledger_path).parent / "gate-adjudications.json"
    adjudicated = set(json.loads(adj_path.read_text())["adjudicated_pass"]) if adj_path.exists() else set()
    rows = [r for r in rows if r.get("rep") != 99 and r["role"] == "run_total"
            and r["variant"] in ARM_TO_CELL
            and (r.get("gate") == "pass" or r["run_id"] in adjudicated)
            and (include is None or include(r))]
    per_run = {}
    for r in rows:
        tr = Path(runs_dir) / r["run_id"] / "transcript.jsonl"
        if not tr.exists():
            continue
        turns, s0, cmax = _turn_stats(tr)
        per_run[r["run_id"]] = {"cell": ARM_TO_CELL[r["variant"]], "n": r["n"],
                                "turns": turns, "s0": s0, "cmax": cmax,
                                "out": r["output_tokens"], "cost": r["cost_usd"],
                                "wall": r["wall_s"]}
    cells = {}
    groups = {}
    for v in per_run.values():
        groups.setdefault(v["cell"], []).append(v)

    def med(vals):
        return statistics.median(vals) if vals else None

    # 기준 비율: flat/opus 2점 fit (env vs ep 분해)
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

    ref = two_point(groups.get(("flat", "opus"), []))

    for cell, runs in groups.items():
        if len(runs) < min_runs:
            continue  # 표본 부족 셀은 insufficient_calibration으로 처리됨
        s0 = med([r["s0"] for r in runs])
        tp = two_point(runs)
        approx = False
        if tp is None:
            # 단일 N 셀: flat/opus의 env:ep 비율 차용
            if ref is None:
                continue
            n = runs[0]["n"]
            def split(total, env_ref, ep_ref):
                ratio = env_ref / (env_ref + ep_ref * n) if (env_ref + ep_ref * n) else 0
                env = total * ratio
                return env, (total - env) / n
            d_env, d_ep = split(med([r["cmax"] - r["s0"] for r in runs]), ref[0], ref[1])
            t_env, t_ep = split(med([r["turns"] for r in runs]), ref[2], ref[3])
            o_env, o_ep = split(med([r["out"] for r in runs]), ref[4], ref[5])
            approx = True
        else:
            d_env, d_ep, t_env, t_ep, o_env, o_ep = tp
        total_turns = med([r["turns"] for r in runs])
        # run 분산 밴드용 실측 비용은 셀의 최대 N(전체 규모 지점)에서 취한다.
        # 구 구현은 n == 8 리터럴이었다 — SmartPlant(최대 N=8) 전제가 새어나온 것으로,
        # 최대 N이 다른 프로젝트에서 빈 배열이 되어 밴드가 기본값으로 조용히 퇴화했다.
        max_n = max(r["n"] for r in runs)
        cells["/".join(cell)] = {
            "S0": s0, "delta_env": d_env, "delta_ep": d_ep,
            "tau_env": t_env, "tau_ep": t_ep, "out_env": o_env, "out_ep": o_ep,
            "latency_s_per_turn": med([r["wall"] / max(r["turns"], 1) for r in runs]),
            "measured_costs": sorted(round(r["cost"], 2) for r in runs if r["n"] == max_n),
            "n_runs": len(runs), "env_split_approx": approx,
        }
    return {"version": "kn-cal-1", "source": str(ledger_path), "pricing": PRICING,
            "alpha_default": 0.5, "cells": cells}


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
    text = json.dumps(cal, indent=1, ensure_ascii=False)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out} ({len(cal['cells'])} cells)")
    else:
        print(text)


if __name__ == "__main__":
    main()
