#!/usr/bin/env python3
"""kn-estimator CLI: 대상 프로젝트 정적 스캔 → N·w 분포·청크 플랜·비용 예측.

사용:
  python3 estimate.py <project_root> [--mode template|flat] [--model sonnet|opus|haiku]
                      [--calibration cal.json] [--w-soft 180000] [--w-hard 900000]
                      [--conservative] [--parallel] [--out-dir .kn]

출력: <out-dir>/kn-report.md (사람용), <out-dir>/kn-plan.json (기계용).
LLM 호출 없음 — 파일 스캔만으로 수 초 내 동작.
"""
import argparse, json, sys
from pathlib import Path

from . import model, plan as plan_mod, scan

HERE = Path(__file__).resolve().parent
BUNDLED_CALIBRATION = HERE / "data/calibration.json"


def load_calibration(path=None):
    """캘리브레이션 로드 — 기본은 패키지 동봉본.

    시딩 시점에는 실행마다 `results/`(23MB 트랜스크립트)를 읽어 재계산했다. 그 의존이
    도구를 저장소에 묶었다. 이제 사전 산출본을 동봉하고, 원장은 `kn-calibrate`로
    오프라인 재생성한다. 부재·파손 시 조용히 폴백하지 않고 재생성 방법을 알린다.
    """
    p = Path(path) if path else BUNDLED_CALIBRATION
    if not p.exists():
        raise SystemExit(
            f"캘리브레이션을 찾을 수 없다: {p}\n"
            "재생성: kn-calibrate --ledger results/run_ledger.jsonl --runs results/runs "
            f"--out {BUNDLED_CALIBRATION}")
    try:
        cal = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"캘리브레이션 파싱 실패: {p}\n{e}")
    if not cal.get("cells"):
        raise SystemExit(f"캘리브레이션에 셀이 없다: {p}")
    return cal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--mode", default="template", choices=["template", "flat"])
    ap.add_argument("--model", default="sonnet", choices=["sonnet", "opus", "haiku"])
    ap.add_argument("--calibration")
    ap.add_argument("--w-soft", type=int, default=plan_mod.W_SOFT_DEFAULT)
    ap.add_argument("--w-hard", type=int, default=plan_mod.W_HARD_DEFAULT)
    ap.add_argument("--conservative", action="store_true", help="W_soft=150K 보수 프리셋")
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--out-dir", default=".kn")
    args = ap.parse_args()
    w_soft = 150_000 if args.conservative else args.w_soft

    cal = load_calibration(args.calibration)

    eps = scan.inventory(args.project_root)
    if not eps:
        print("No JSON endpoints found."); sys.exit(1)
    sls = scan.build_slices(args.project_root, eps)
    ws = sorted(s["w_tokens"] for s in sls)
    n = len(sls)
    tertiles = ws[n // 3], ws[2 * n // 3]
    unresolved = sum(1 for s in sls if s["unresolved"])

    p = plan_mod.build_plan(sls, cal, mode=args.mode, mdl=args.model,
                            w_hard=args.w_hard, w_soft=w_soft, parallel=args.parallel)

    matrix = {}
    w_mean = sum(ws) / n
    whs = [w / w_mean for w in ws]
    for mode in ("flat", "template"):
        for mdl in ("opus", "sonnet", "haiku"):
            key = f"{mode}/{mdl}"
            if key not in cal["cells"]:
                matrix[key] = "insufficient_calibration"
                continue
            pm = plan_mod.build_plan(sls, cal, mode=mode, mdl=mdl,
                                     w_hard=args.w_hard, w_soft=w_soft)
            matrix[key] = {"total_cost_usd": pm["total_cost_usd"],
                           "n_chunks": pm["n_chunks"], "k_avg": pm["k_avg"],
                           "wall_h": round(pm["total_wall_s"] / 3600, 1)}

    out = Path(args.project_root) / args.out_dir
    out.mkdir(exist_ok=True)
    plan_json = {**{k: v for k, v in p.items() if k != "chunks"},
                 "chunks": [{**c, "endpoints": [f"{s['endpoint']['method']} {s['endpoint']['path']}"
                                                 for s in c["endpoints"]]} for c in p["chunks"]],
                 "calibration_version": cal["version"]}
    (out / "kn-plan.json").write_text(json.dumps(plan_json, indent=2, ensure_ascii=False))

    top = sorted(sls, key=lambda s: -s["w_tokens"])[:10]
    lines = [
        "# kn-estimator 보고서", "",
        "> **절대 USD는 비보증** — 캘리브레이션은 단일 프로젝트(LegacySut) 실측 기반이며,",
        "> 이 보고서의 주 용도는 모드·모델·청크 구성의 **상대 비교**다. 토큰 추정은 bytes/4",
        "> 근사(fallback)를 사용한다.", "",
        f"- 대상: `{args.project_root}`",
        f"- **N = {n}** 엔드포인트 (미해결 슬라이스 {unresolved}건 = {unresolved/n:.0%} — 중앙값 prior 적용)",
        f"- w 분포: p33={tertiles[0]:,} / p66={tertiles[1]:,} / max={ws[-1]:,} tokens (상대 비교용)", "",
        f"## 권장 플랜 ({args.mode}×{args.model})", "",
        f"- 청크 수: **{p['n_chunks']}** (평균 K={p['k_avg']})",
        f"- 예상 총비용: **${p['total_cost_usd']}** / 예상 벽시계: {p['total_wall_s']/3600:.1f}h"
        f" ({'병렬' if args.parallel else '순차'})",
        f"- 벽: W_soft={w_soft:,} (품질 정책), W_hard={args.w_hard:,} (모델 상한)",
        f"- soft 초과 청크: {sum(1 for c in p['chunks'] if c['soft_exceeded'])}건", "",
        "## 모드×모델 매트릭스 (동일 플랜 로직)", "",
        "| 구성 | 총비용 | 청크 수 | 평균 K | 벽시계 |", "|---|---|---|---|---|"]
    for key, v in matrix.items():
        if isinstance(v, str):
            lines.append(f"| {key} | {v} | — | — | — |")
        else:
            lines.append(f"| {key} | ${v['total_cost_usd']} | {v['n_chunks']} | {v['k_avg']} | {v['wall_h']}h |")
    lines += ["", "## 복잡도 상위 10 엔드포인트", "",
              "| Endpoint | w (tokens) | external | unresolved |", "|---|---|---|---|"]
    for s in top:
        e = s["endpoint"]
        lines.append(f"| {e['method']} {e['path']} | {s['w_tokens']:,} "
                     f"| {'Y' if s['external_call'] else ''} | {', '.join(s['unresolved'])} |")
    lines += ["", "## 한계 고지", "",
              "- 캘리브레이션 셀별 실측 run 분산(±30~46%)이 예측 하한 오차 — 구간으로 해석할 것.",
              "- 정적 슬라이스는 리플렉션·동적 라우팅·설정 기반 빈을 과소평가할 수 있음.",
              f"- 캘리브레이션 버전: {cal['version']} (N=8 관측 기반 — 대규모 N 외삽 미검증).",
              "- 미캘리브레이션 셀은 insufficient_calibration으로 표기 (추정치 미제공)."]
    (out / "kn-report.md").write_text("\n".join(lines) + "\n")
    print(f"N={n} chunks={p['n_chunks']} k_avg={p['k_avg']} est=${p['total_cost_usd']}")
    print(f"report: {out/'kn-report.md'}\nplan:   {out/'kn-plan.json'}")


if __name__ == "__main__":
    main()
