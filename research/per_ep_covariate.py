#!/usr/bin/env python3
"""w 공변량의 경험적 근거를 실측 트랜스크립트로 검정한다.

비용 모델은 EP별 작업량을 `k = (w_i / w̄)^α`로 스케일하고 τ·δ·out 셋 다에 곱한다
(`model.py`). α는 `calibrate.py`가 `0.5`로 하드코딩한다 — 설계는 로그선형 fit을
요구했으나 구현되지 않았다(리뷰 K2).

이 스크립트는 트랜스크립트에서 **EP별 관측치를 복원**해 그 전제를 검정한다:

1. 엔드포인트 목록은 run 초기 프롬프트에 있다.
2. template 모드는 EP당 산출물(스펙 JSON) 1개를 정확히 쓴다 → Write 도구의
   `content` 길이가 그 EP의 출력량이고, Write 시점 컨텍스트의 연속 차이가 δ다.
3. 산출물 파일명 ↔ 엔드포인트는 경로·핸들러 토큰 겹침으로 매칭한다.

실행: python research/per_ep_covariate.py   (설치된 패키지 + ./smartplant 필요)
"""
import json
import math
import re
import statistics
from pathlib import Path

from kn_estimator import scan

REPO = Path(__file__).resolve().parents[1]
SUT = REPO / "smartplant"
RUNS_DIR = REPO / "results/runs"
# template 셀만 쓴다 — flat은 테스트 파일 외 지원 파일도 써서 EP와 1:1이 아니다.
RUNS = ["flat_template-n8-r1", "flat_template-n8-r2", "flat_template-n8-r3",
        "flat_template_sonnet-n8-r1", "flat_template_sonnet-n8-r2", "flat_template_sonnet-n8-r3"]

EP_RE = re.compile(r"^- (\w+) (\S+)\s+\(controller: (\S+), handler: (\w+)\)", re.M)
BRANCH_RE = re.compile(r"\b(if|for|while|case|catch)\b|&&|\|\||\?")


def _records(run):
    return [json.loads(l) for l in (RUNS_DIR / run / "transcript.jsonl").read_text().splitlines()]


def prompt_endpoints(recs):
    r = next(x for x in recs if x.get("type") == "user")
    c = (r.get("message") or {}).get("content")
    txt = c if isinstance(c, str) else " ".join(
        b.get("text", "") for b in c if isinstance(b, dict))
    return [{"method": m, "path": p, "file": f, "handler": h, "controller": Path(f).stem}
            for m, p, f, h in EP_RE.findall(txt)]


def artifact_writes(recs):
    """(산출물 stem, 작성 바이트, 그 시점 컨텍스트) — Write 순서대로."""
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
                    if fp and content:
                        out.append((Path(fp).stem, len(content.encode()), ctx))
    return out


def _tokens(s):
    return set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower()))


def match_endpoints(eps, names):
    """엔드포인트 ↔ 산출물명: 토큰 겹침 최대, 최소 2개 겹쳐야 채택."""
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
            pairs[f"{e['method']} {e['path']}"] = best
            used.add(best)
    return pairs


def slice_branches(sl):
    total = 0
    for f in sl["files"]:
        p = SUT / f
        if p.suffix == ".java":
            total += len(BRANCH_RE.findall(p.read_text(errors="replace")))
    return total + 1


def collect():
    out_rows, delta_rows = [], []
    for run in RUNS:
        if not (RUNS_DIR / run / "transcript.jsonl").exists():
            continue
        recs = _records(run)
        eps = prompt_endpoints(recs)
        writes = artifact_writes(recs)
        if not eps or len(writes) < 8:
            continue
        pairs = match_endpoints(eps, [n for n, _, _ in writes])
        sls = {f"{s['endpoint']['method']} {s['endpoint']['path']}": s
               for s in scan.build_slices(str(SUT), eps)}
        by_name = {n: k for k, n in pairs.items()}
        for i, (name, nbytes, ctx) in enumerate(writes):
            key = by_name.get(name)
            if not key:
                continue
            sl = sls[key]
            out_rows.append({"w": sl["w_tokens"], "out": nbytes,
                             "branches": slice_branches(sl)})
            if i > 0:
                d = ctx - writes[i - 1][2]
                if d > 0:
                    delta_rows.append({"w": sl["w_tokens"], "delta": d})
    return out_rows, delta_rows


def loglinear_alpha(rows, ykey):
    """log(y) = c + α·log(w/w̄) 를 fit해 (α, R²) 반환."""
    wbar = statistics.mean([r["w"] for r in rows])
    xs = [math.log(r["w"] / wbar) for r in rows]
    ys = [math.log(r[ykey]) for r in rows]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    alpha = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    c = my - alpha * mx
    ss_res = sum((y - (c + alpha * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return alpha, (1 - ss_res / ss_tot if ss_tot else 0.0)


def r_squared(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return (sxy ** 2) / (sxx * syy) if sxx and syy else 0.0


def main():
    if not SUT.exists():
        raise SystemExit(f"SUT 없음: {SUT}")
    out_rows, delta_rows = collect()
    ws = sorted({r["w"] for r in out_rows})
    print(f"관측: out {len(out_rows)}건 / δ {len(delta_rows)}건")
    print(f"서로 다른 엔드포인트(=실질 독립 표본): {len(ws)}   w 범위 {min(ws):,}~{max(ws):,} "
          f"({max(ws)/min(ws):.1f}배)\n")

    a_out, r2_out = loglinear_alpha(out_rows, "out")
    a_del, r2_del = loglinear_alpha(delta_rows, "delta")
    print("α 로그선형 fit (설계 K2가 요구했으나 미구현):")
    print(f"  out 채널: α = {a_out:+.3f}   R² = {r2_out:.3f}")
    print(f"  δ  채널: α = {a_del:+.3f}   R² = {r2_del:.3f}")
    print(f"  하드코딩 기본값: α = 0.5\n")

    print("복잡도 vs 크기 (C1 — 분기 수가 크기보다 나은 예측변수인가):")
    outs = [r["out"] for r in out_rows]
    print(f"  out ~ 슬라이스 크기 : R² = {r_squared([r['w'] for r in out_rows], outs):.3f}")
    print(f"  out ~ 슬라이스 분기 : R² = {r_squared([r['branches'] for r in out_rows], outs):.3f}")
    print(f"  크기 vs 분기 상관   : R² = "
          f"{r_squared([r['w'] for r in out_rows], [r['branches'] for r in out_rows]):.3f}"
          "  ← 높으면 복잡도가 크기의 대리변수일 뿐")

    med = statistics.median([r["w"] for r in delta_rows])
    lo = [r["delta"] for r in delta_rows if r["w"] <= med]
    hi = [r["delta"] for r in delta_rows if r["w"] > med]
    print(f"\nδ가 w에 반응하는가 (α=0.5라면 w {max(ws)/min(ws):.0f}배 범위에서 "
          f"~{(max(ws)/min(ws))**0.5:.1f}배를 기대):")
    print(f"  w 하위 절반 δ 중앙값 {statistics.median(lo):,.0f}  → "
          f"상위 절반 {statistics.median(hi):,.0f}  = {statistics.median(hi)/statistics.median(lo):.2f}배")
    ds = [r["delta"] for r in delta_rows]
    print(f"  (δ 자체는 {min(ds):,}~{max(ds):,}로 크게 변동한다 — 측정이 죽은 게 아니다)")


if __name__ == "__main__":
    main()
