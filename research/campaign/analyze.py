"""캠페인 분석 — ① 프로젝트별 캘리브레이션 + 계수 비교표 ② α 재검정 (다중 프로젝트 풀링).

① kn_estimator.calibrate를 캠페인 원장에 적용해 프로젝트별 calibration을 만들고,
   동봉(LegacySut) 계수와 나란히 놓는다 — "계수가 프로젝트마다 얼마나 다른가"가
   이 캠페인의 1차 질문이다.
② research/per_ep_covariate.py의 복원 방법(template 모드: Write 1건 = EP 1개)을
   여러 프로젝트에 일반화해 EP별 (w, δ, out) 관측을 풀링하고 α를 로그선형 fit한다.
   LegacySut 단독 검정(w 범위 24.3배, EP 8개)의 확장이다.

실행: .venv/bin/python research/campaign/analyze.py
"""
import json
import math
import re
import statistics
from pathlib import Path

from kn_estimator import calibrate as C
from kn_estimator import scan

HERE = Path(__file__).parent
REPO = HERE.parent.parent
CAMPAIGN = REPO / "results/campaign"
BUNDLED = json.loads((REPO / "src/kn_estimator/data/calibration.json").read_text())

EP_RE = re.compile(r"^- (\w+) (\S+)\s+\(controller: (\S+), handler: (\w+)\)", re.M)


def targets():
    return {k: v for k, v in json.loads((HERE / "targets.json").read_text()).items()
            if not k.startswith("_")}


# ---- ① 프로젝트별 캘리브레이션 비교 -------------------------------------------

def per_project_calibrations():
    cals = {}
    for name in targets():
        ledger = CAMPAIGN / name / "run_ledger.jsonl"
        runs = CAMPAIGN / name / "runs"
        if not ledger.exists():
            continue
        cals[name] = C.calibrate(ledger, runs, min_runs=2)
    return cals


def comparison_table(cals):
    rows = []
    fields = ["S0", "delta_env", "delta_ep", "tau_env", "tau_ep", "out_env", "out_ep",
              "latency_s_per_turn", "n_runs"]
    all_cells = sorted({c for cal in cals.values() for c in cal["cells"]}
                       | set(BUNDLED["cells"]))
    for cell in all_cells:
        for src_name, cal in [("legacy-sut(동봉)", BUNDLED)] + sorted(cals.items()):
            v = cal["cells"].get(cell)
            if not v:
                continue
            rows.append([cell, src_name] + [round(v[f], 1) if isinstance(v[f], float) else v[f]
                                            for f in fields])
    head = ["cell", "project"] + fields
    widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    fmt = lambda r: " | ".join(str(x).ljust(w) for x, w in zip(r, widths))
    return "\n".join([fmt(head), "-|-".join("-" * w for w in widths)] + [fmt(r) for r in rows])


# ---- ② α 재검정 (per_ep_covariate 일반화) ------------------------------------

def _records(tr):
    return [json.loads(l) for l in tr.read_text().splitlines()]


def prompt_endpoints(recs):
    r = next(x for x in recs if x.get("type") == "user")
    c = (r.get("message") or {}).get("content")
    txt = c if isinstance(c, str) else " ".join(
        b.get("text", "") for b in c if isinstance(b, dict))
    return [{"method": m, "path": p, "file": f, "handler": h, "controller": Path(f).stem}
            for m, p, f, h in EP_RE.findall(txt)]


def artifact_writes(recs):
    """(산출물 stem, 바이트, 그 시점 컨텍스트) — .nimbus/specs Write만, 순서대로."""
    seen, ctx, out = set(), 0, []
    for r in recs:
        m = r.get("message") or {}
        if r.get("type") == "assistant" and m.get("id") and m["id"] not in seen:
            seen.add(m["id"])
            u = m.get("usage") or {}
            ctx = (u.get("cache_read_input_tokens", 0) + u.get("input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Write":
                    i = b.get("input") or {}
                    fp, content = i.get("file_path", ""), i.get("content", "")
                    if fp and content and "/specs/" in fp:
                        out.append((Path(fp).stem, len(content.encode()), ctx))
    return out


def _tokens(s):
    return set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower()))


def match_endpoints(eps, names):
    pairs, used = {}, set()
    for e in eps:
        et = _tokens(e["path"]) | _tokens(e["handler"])
        best, score = None, 0
        for n in names:
            if n in used:
                continue
            s = len(et & _tokens(n))
            if s > score:
                best, score = n, s
        if best and score >= 2:
            pairs[best] = e
            used.add(best)
    return pairs


def alpha_observations():
    """전 프로젝트 template run에서 (project, ep, w_hat, out_bytes, delta) 수집."""
    obs = []
    for name, t in targets().items():
        runs_dir = CAMPAIGN / name / "runs"
        if not runs_dir.exists():
            continue
        inv = scan.inventory(t["src"])
        sls = scan.build_slices(t["src"], inv)
        w_by_key = {(e["method"], e["path"]): s["w_tokens"]
                    for e, s in zip(inv, sls)}
        w_mean = statistics.mean(w_by_key.values())
        for run in sorted(runs_dir.iterdir()):
            if "template" not in run.name:
                continue
            tr = run / "transcript.jsonl"
            if not tr.exists():
                continue
            recs = _records(tr)
            eps = prompt_endpoints(recs)
            writes = artifact_writes(recs)
            pairs = match_endpoints(eps, [w[0] for w in writes])
            prev_ctx = None
            for stem, nbytes, ctx in writes:
                e = pairs.get(stem)
                if e:
                    delta = (ctx - prev_ctx) if prev_ctx is not None else None
                    w = w_by_key.get((e["method"], e["path"]))
                    if w:
                        obs.append({"project": name, "run": run.name,
                                    "ep": f"{e['method']} {e['path']}",
                                    "w_hat": w / w_mean, "out": nbytes, "delta": delta})
                prev_ctx = ctx
    return obs


def fit_alpha(obs, field):
    pts = [(o["w_hat"], o[field]) for o in obs if o.get(field) and o[field] > 0 and o["w_hat"] > 0]
    if len(pts) < 4:
        return None, None, len(pts)
    xs = [math.log(w) for w, _ in pts]
    ys = [math.log(v) for _, v in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 0
    ss_res = sum((y - (my + a * (x - mx))) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0
    return a, r2, n


def main():
    cals = per_project_calibrations()
    print("== ① 셀 계수 비교 (LegacySut 동봉 vs 캠페인 프로젝트) ==\n")
    print(comparison_table(cals))
    out_dir = CAMPAIGN / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, cal in cals.items():
        (out_dir / f"calibration-{name}.json").write_text(
            json.dumps(cal, indent=1, ensure_ascii=False))
    print(f"\n(프로젝트별 calibration JSON → {out_dir}/)")

    print("\n== ② α 재검정 (template run, EP별 복원, 프로젝트 풀링) ==\n")
    obs = alpha_observations()
    by_proj = {}
    for o in obs:
        by_proj.setdefault(o["project"], []).append(o)
    for scope, data in [("pooled(전체)", obs)] + sorted(by_proj.items()):
        a_out, r2_out, n_out = fit_alpha(data, "out")
        a_d, r2_d, n_d = fit_alpha(data, "delta")
        w_range = (max(o["w_hat"] for o in data) / min(o["w_hat"] for o in data)) if data else 0
        print(f"{scope}: obs={len(data)} w_range={w_range:.1f}x | "
              f"α(out)={a_out if a_out is None else round(a_out, 3)} R²={r2_out if r2_out is None else round(r2_out, 3)} (n={n_out}) | "
              f"α(δ)={a_d if a_d is None else round(a_d, 3)} R²={r2_d if r2_d is None else round(r2_d, 3)} (n={n_d})")
    (out_dir / "alpha-observations.json").write_text(json.dumps(obs, indent=1, ensure_ascii=False))
    print(f"(관측 원본 → {out_dir}/alpha-observations.json)")


if __name__ == "__main__":
    main()
