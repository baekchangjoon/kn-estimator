#!/usr/bin/env python3
"""단위(컨트롤러)별 비용 계수 분화의 경험적 근거 검정.

질문: c(2차 계수)를 가르는 δ_ep·out_ep가 **컨트롤러 소속**에 따라 유의하게 다른가?
다르면 컨트롤러 단위 c 캘리브레이션에 근거가 생기고, 다르지 않으면 "전역 계수로
충분"이 데이터의 답이다 (§4.7의 w 공변량 검정과 같은 정신 — 만들기 전에 검정).

데이터:
1. 캠페인 per-EP 관측 103건 (`results/campaign/analysis/alpha-observations.json`,
   project/ep/out/delta). EP→컨트롤러 매핑은 대상 저장소를 스캔해 얻는다
   (`--repo <project>=<path>` 인자, 반복 지정).
2. LegacySut template run 6건의 트랜스크립트 — 초기 프롬프트에 controller가
   명시돼 있어 SUT 없이 복원 가능 (out: 산출물 Write 바이트, δ: Write 간 컨텍스트 차).

방법: run 반복을 EP 중앙값으로 접고(EP가 독립 표본 단위), 컨트롤러로 그룹핑해
일원 분산분석 — η²(집단간 분산 비율)와 순열검정 p값(라벨 셔플 10,000회, seed 0).
표본이 작으므로(프로젝트당 EP 5~13) p와 η²를 함께 보고 과잉 해석하지 않는다.

실행 예:
  python research/unit_variance.py \
    --repo petclinic=/path/spring-petclinic \
    --repo auth-user=/path/tainted-spring-auth-user \
    --repo community=/path/tainted-spring-community
"""
import argparse
import json
import random
import statistics
from pathlib import Path

from kn_estimator import scan

REPO = Path(__file__).resolve().parents[1]
CAMPAIGN_OBS = REPO / "results/campaign/analysis/alpha-observations.json"
LEGACY-SUT_RUNS = ["flat_template-n8-r1", "flat_template-n8-r2", "flat_template-n8-r3",
                   "flat_template_sonnet-n8-r1", "flat_template_sonnet-n8-r2",
                   "flat_template_sonnet-n8-r3"]


def controller_map(repo_path):
    """저장소 스캔 → {"METHOD /path": controller 클래스명}."""
    return {f"{e['method']} {e['path']}": Path(e["file"]).stem
            for e in scan.inventory(str(repo_path))}


def legacy-sut_observations():
    """SUT 없이 트랜스크립트만으로 (ep, controller, out, delta) 복원."""
    import per_ep_covariate as pec
    rows = []
    for run in LEGACY-SUT_RUNS:
        tr = REPO / "results/runs" / run / "transcript.jsonl"
        if not tr.exists():
            continue
        recs = pec._records(run)
        eps = pec.prompt_endpoints(recs)
        writes = pec.artifact_writes(recs)
        pairs = pec.match_endpoints(eps, [n for n, _, _ in writes])
        ctrl = {f"{e['method']} {e['path']}": e["controller"] for e in eps}
        by_name = {n: k for k, n in pairs.items()}
        for i, (name, nbytes, ctx) in enumerate(writes):
            key = by_name.get(name)
            if not key:
                continue
            delta = None
            if i > 0:
                d = ctx - writes[i - 1][2]
                if d > 0:
                    delta = d
            rows.append({"project": "legacy-sut", "ep": key,
                         "controller": ctrl[key], "out": nbytes, "delta": delta})
    return rows


def anova(groups):
    """일원 분산분석: (η², F). groups = {label: [values]}."""
    all_vals = [v for vs in groups.values() for v in vs]
    if len(groups) < 2 or len(all_vals) <= len(groups):
        return None
    gm = statistics.mean(all_vals)
    ssb = sum(len(vs) * (statistics.mean(vs) - gm) ** 2 for vs in groups.values())
    ssw = sum((v - statistics.mean(vs)) ** 2 for vs in groups.values() for v in vs)
    sst = ssb + ssw
    if sst == 0:
        return None
    dfb, dfw = len(groups) - 1, len(all_vals) - len(groups)
    f_stat = (ssb / dfb) / (ssw / dfw) if ssw > 0 and dfw > 0 else float("inf")
    return {"eta2": ssb / sst, "F": f_stat, "k": len(groups), "n": len(all_vals)}


def permutation_p(labels, values, observed_eta2, iters=10_000, seed=0):
    """라벨을 섞어 η² 영분포를 만들고 관측 η² 이상 비율을 p로 반환."""
    rng = random.Random(seed)
    hits = 0
    lab = list(labels)
    for _ in range(iters):
        rng.shuffle(lab)
        groups = {}
        for l, v in zip(lab, values):
            groups.setdefault(l, []).append(v)
        a = anova(groups)
        if a and a["eta2"] >= observed_eta2 - 1e-12:
            hits += 1
    return hits / iters


def per_ep_median(rows, channel):
    """run 반복을 EP 중앙값으로 접는다 → [(controller, value)]."""
    by_ep = {}
    for r in rows:
        v = r.get(channel)
        if v is None:
            continue
        by_ep.setdefault((r["controller"], r["ep"]), []).append(v)
    return [(c, statistics.median(vs)) for (c, _), vs in sorted(by_ep.items())]


def test_project(name, rows, results):
    for channel in ("out", "delta"):
        pairs = per_ep_median(rows, channel)
        groups = {}
        for c, v in pairs:
            groups.setdefault(c, []).append(v)
        a = anova(groups)
        if a is None:
            print(f"  {name}/{channel}: 검정 불가 (컨트롤러 {len(groups)}개, EP {len(pairs)}개)")
            continue
        p = permutation_p([c for c, _ in pairs], [v for _, v in pairs], a["eta2"])
        meds = {c: round(statistics.median(vs)) for c, vs in sorted(groups.items())}
        print(f"  {name}/{channel}: η²={a['eta2']:.3f} F={a['F']:.2f} "
              f"(컨트롤러 {a['k']}, EP {a['n']}) 순열 p={p:.3f}  중앙값={meds}")
        results.append({"project": name, "channel": channel, **a, "p": p,
                        "medians": meds})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", default=[],
                    metavar="PROJECT=PATH", help="캠페인 프로젝트의 저장소 경로")
    ap.add_argument("--out", type=Path, help="결과 JSON 저장 경로")
    args = ap.parse_args()
    repos = dict(kv.split("=", 1) for kv in args.repo)

    results = []
    print("== 컨트롤러 소속이 per-EP 관측(out·δ)의 분산을 설명하는가 ==")

    obs = json.loads(CAMPAIGN_OBS.read_text()) if CAMPAIGN_OBS.exists() else []
    for project in sorted({o["project"] for o in obs}):
        if project not in repos:
            print(f"  {project}: --repo 미지정, 생략")
            continue
        cmap = controller_map(repos[project])
        rows = [{**o, "controller": cmap.get(o["ep"], "?")}
                for o in obs if o["project"] == project]
        unmapped = sum(1 for r in rows if r["controller"] == "?")
        if unmapped:
            print(f"  {project}: 매핑 실패 {unmapped}건 제외")
            rows = [r for r in rows if r["controller"] != "?"]
        test_project(project, rows, results)

    sp = legacy-sut_observations()
    if sp:
        test_project("legacy-sut", sp, results)

    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
