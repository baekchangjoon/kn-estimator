"""청크 파티션 최적화 (설계 v2.3).

컨트롤러 단위 묶음 → δ̂ 내림차순 First-Fit-Decreasing (W_target 용량)
→ 파티션 전체를 시뮬레이션해 총비용 평가 → W_target 그리드에서 최소 선택.
벽은 이층: W_hard(모델 상한, 위반 불가) / W_soft(품질 정책, 초과 시 패널티·경고).
"""
from . import model

W_HARD_DEFAULT = 900_000     # 1M 윈도우 - 여유분
W_SOFT_DEFAULT = 180_000     # 실측 flat N=8 종료 분포 p50 역산 (보수 프리셋: 150K)


def _controller_groups(slices):
    groups = {}
    for s in slices:
        groups.setdefault(s["endpoint"]["controller"], []).append(s)
    return list(groups.values())


def _delta_hat(cal, mode, mdl, s, w_mean):
    c = cal["cells"][f"{mode}/{mdl}"]
    return c["delta_ep"] * ((s["w_tokens"] / w_mean) ** cal.get("alpha_default", 0.5))


def _pack(groups, cap, delta_of):
    """그룹(컨트롤러) 단위 FFD. 그룹이 cap을 넘으면 그룹 내부 분할."""
    items = sorted(groups, key=lambda g: -sum(delta_of(s) for s in g))
    bins = []  # (남은 용량, slices)
    for g in items:
        need = sum(delta_of(s) for s in g)
        parts = [g]
        if need > cap:  # 단일 컨트롤러가 초과 → EP 단위 분할
            parts, cur, acc = [], [], 0
            for s in sorted(g, key=lambda s: -delta_of(s)):
                if cur and acc + delta_of(s) > cap:
                    parts.append(cur); cur, acc = [], 0
                cur.append(s); acc += delta_of(s)
            if cur:
                parts.append(cur)
        for part in parts:
            pneed = sum(delta_of(s) for s in part)
            for b in bins:
                if b[0] >= pneed:
                    b[0] -= pneed; b[1].extend(part)
                    break
            else:
                bins.append([cap - pneed, list(part)])
    return [b[1] for b in bins]


def build_plan(slices, cal, mode="template", mdl="sonnet",
               w_hard=W_HARD_DEFAULT, w_soft=W_SOFT_DEFAULT, parallel=False):
    cell = cal["cells"].get(f"{mode}/{mdl}")
    if cell is None:
        return {"status": "insufficient_calibration", "mode": mode, "model": mdl}
    w_mean = sum(s["w_tokens"] for s in slices) / len(slices)
    delta_of = lambda s: _delta_hat(cal, mode, mdl, s, w_mean)
    groups = _controller_groups(slices)
    budget_soft = max(w_soft - cell["S0"] - cell["delta_env"], cell["delta_ep"])

    best = None
    for frac in (0.4, 0.55, 0.7, 0.85, 1.0):   # W_target 그리드
        cap = budget_soft * frac
        partition = _pack(groups, cap, delta_of)
        total, wall, chunks, ok = 0.0, 0.0, [], True
        for part in partition:
            whs = [s["w_tokens"] / w_mean for s in part]
            sim = model.simulate_chunk(cal, mode, mdl, whs)
            if sim["peak_context"] > w_hard:
                ok = False
                break
            soft_penalty = 1.15 if sim["peak_context"] > w_soft else 1.0
            total += sim["cost_usd"] * soft_penalty
            wall = max(wall, sim["wall_s"]) if parallel else wall + sim["wall_s"]
            chunks.append({"endpoints": part, "n_endpoints": len(part),
                           "est_cost_usd": round(sim["cost_usd"], 2),
                           "est_peak_context": int(sim["peak_context"]),
                           "soft_exceeded": sim["peak_context"] > w_soft,
                           "est_wall_s": int(sim["wall_s"])})
        if not ok:
            continue
        if parallel:  # 병렬은 청크마다 프리픽스 cache_write 재발생 — 근사 할증
            total *= 1.05
        if best is None or total < best["total_cost_usd"]:
            best = {"w_target_frac": frac, "total_cost_usd": round(total, 2),
                    "total_wall_s": int(wall), "n_chunks": len(chunks),
                    "chunks": chunks}
    if best is None:
        # 모든 W_target frac에서 어떤 청크의 peak_context가 w_hard를 넘었다. 크래시 대신
        # 상태를 돌려준다 — 호출자가 벽을 올리거나 모델을 바꿔야 하는 상황이다.
        return {"status": "infeasible_w_hard", "mode": mode, "model": mdl,
                "w_hard": w_hard, "w_soft": w_soft,
                "reason": "모든 W_target 후보에서 청크 peak_context가 w_hard를 초과했다. "
                          "--w-hard를 올리거나 컨텍스트가 더 큰 모델을 지정하라."}
    best.update({"mode": mode, "model": mdl, "w_hard": w_hard, "w_soft": w_soft,
                 "parallel": parallel,
                 "k_avg": round(len(slices) / best["n_chunks"], 1)})
    return best
