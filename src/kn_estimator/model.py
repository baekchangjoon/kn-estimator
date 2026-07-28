"""청크 비용 시뮬레이션 (설계 v2.1 — 트랜스크립트 집계와 동일 정의).

cost = P_cr·Σ(τ_i·C) + P_cw·(S0 + Σδ_i) + P_out·Σout_i
δ_i, out_i, τ_i는 셀 캘리브레이션 × ŵ_i^α 공변량 스케일.
env 고정분(delta_env/tau_env/out_env)은 청크당 1회.
"""


def _cell(cal, label, mdl):
    return cal["cells"].get(f"{label}/{mdl}")


def simulate_chunk(cal, label, mdl, w_hats, alpha=None):
    c = _cell(cal, label, mdl)
    if c is None:
        return {"status": "insufficient_calibration"}
    alpha = cal.get("alpha_default", 0.5) if alpha is None else alpha
    p = cal["pricing"][mdl]
    p_cr = p["input"] * cal["pricing"]["cache_read_mult"] / 1e6
    p_cw = p["input"] * cal["pricing"]["cache_write_mult"] / 1e6
    p_out = p["output"] / 1e6

    ctx = c["S0"]
    cache_read = c["tau_env"] * (ctx + c["delta_env"] / 2)
    ctx += c["delta_env"]
    cache_write = c["S0"] + c["delta_env"]
    out = c["out_env"]
    turns = c["tau_env"]
    for wh in w_hats:
        k = wh ** alpha
        tau_i, delta_i, out_i = c["tau_ep"] * k, c["delta_ep"] * k, c["out_ep"] * k
        cache_read += tau_i * (ctx + delta_i / 2)
        ctx += delta_i
        cache_write += delta_i
        out += out_i
        turns += tau_i
    cost = p_cr * cache_read + p_cw * cache_write + p_out * out
    wall = turns * c["latency_s_per_turn"]
    return {"cost_usd": cost, "peak_context": ctx, "wall_s": wall,
            "turns": turns, "out_tokens": out}


def _run_variance_band(cal):
    """캘리브레이션 셀들의 run 간 상대 분산을 풀링한 예측구간 밴드 (넓은 CI).

    LLM 행동 분산(동일 조건 3회 ±30~46% 실측)이 예측 하한 오차이므로,
    α 민감도(좁은 CI)와 별도로 곱셈 밴드로 결합한다 (리뷰 K6/S#8).
    """
    import statistics
    ratios = []
    for c in cal["cells"].values():
        mc = c.get("measured_costs") or []
        if len(mc) >= 2:
            m = statistics.median(mc)
            if m:
                ratios += [x / m for x in mc]
    if not ratios:
        return 0.7, 1.3
    return min(0.7, min(ratios)), max(1.3, max(ratios))


def estimate_cell(cal, label, mdl, w_hats):
    """단일 청크 가정의 셀 추정. α 민감도(좁은 구간) × run 분산 밴드(넓은 구간)."""
    base = simulate_chunk(cal, label, mdl, w_hats)
    if base.get("status"):
        return base
    lo = simulate_chunk(cal, label, mdl, w_hats, alpha=0.0)
    hi = simulate_chunk(cal, label, mdl, w_hats, alpha=1.0)
    costs = sorted([lo["cost_usd"], base["cost_usd"], hi["cost_usd"]])
    b_lo, b_hi = _run_variance_band(cal)
    return {**base, "cost_low": costs[0], "cost_high": costs[-1],
            "pi_low": costs[0] * b_lo, "pi_high": costs[-1] * b_hi}
