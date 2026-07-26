"""청크 파티션 최적화 (설계 v2.3).

컨트롤러 단위 묶음 → δ̂ 내림차순 First-Fit-Decreasing (W_target 용량)
→ 파티션 전체를 시뮬레이션해 총비용 평가 → W_target 그리드에서 최소 선택.
벽은 이층: W_hard(모델 상한, 위반 불가) / W_soft(품질 정책, 초과 시 패널티·경고).
"""
from . import model

W_HARD_DEFAULT = 900_000     # 1M 윈도우 - 여유분
W_SOFT_DEFAULT = 180_000     # 실측 flat N=8 종료 분포 p50 역산 (보수 프리셋: 150K)

# 모델별 컨텍스트 윈도우. W_hard는 "모델 상한"이므로 사용자 값과 무관하게 이 상한을
# 넘을 수 없다 — 단일 CLI 값(1M 전제)을 haiku(200K)에 그대로 쓰면 실행 가능성을
# 과대평가한다 (2026-07-26 감사 #1).
MODEL_CONTEXT_WINDOW = {"opus": 1_000_000, "sonnet": 1_000_000, "haiku": 200_000}
W_HARD_WINDOW_FRACTION = 0.9   # 900_000 = 1M × 0.9 — 기존 기본값과 동일한 여유율


def model_w_hard(mdl):
    return int(MODEL_CONTEXT_WINDOW.get(mdl, 1_000_000) * W_HARD_WINDOW_FRACTION)


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
    requested_w_hard = w_hard
    w_hard = min(w_hard, model_w_hard(mdl))
    # W_soft가 유효 W_hard보다 크면 용량 계산이 hard 벽 위반 청크만 만들어 전 후보가
    # 죽는다 (1M 모델 기준으로 조정한 --w-soft를 haiku에 물려주는 흔한 경로). 캡한다.
    w_soft = min(w_soft, w_hard)
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
        # 상태를 돌려준다. 처방은 병목에 따라 갈린다 — 모델 캡이 병목이면 --w-hard
        # 상향은 no-op이므로 그 권고를 내면 사용자가 막다른 길을 돈다.
        # 병목 판정은 "요청값이 캡 이상인가"다 — 캡과 같은 값을 명시한 경우도
        # 상향은 no-op이므로 (w_hard < requested 비교로는 이 경계가 새어나간다).
        if requested_w_hard >= model_w_hard(mdl):
            advice = (f"유효 w_hard는 모델 윈도우 캡({w_hard:,})이라 --w-hard 상향은 "
                      "무효다 — 컨텍스트 윈도우가 더 큰 모델을 지정하라.")
        else:
            advice = "--w-hard를 올리거나 컨텍스트가 더 큰 모델을 지정하라."
        return {"status": "infeasible_w_hard", "mode": mode, "model": mdl,
                "w_hard": w_hard, "w_soft": w_soft,
                "requested_w_hard": requested_w_hard,
                "reason": "모든 W_target 후보에서 청크 peak_context가 w_hard를 초과했다. "
                          + advice}
    best.update({"mode": mode, "model": mdl, "w_hard": w_hard, "w_soft": w_soft,
                 "parallel": parallel,
                 "k_avg": round(len(slices) / best["n_chunks"], 1)})
    return best


def cost_coefficients(cal, mode, mdl):
    """비용 곡선 C(K) = a + b·K + c·K² 의 계수 — 균일 ŵ=1에서 simulate_chunk의
    닫힌 형태 합성 (단일 청크, USD).

    a: 청크당 고정비 (지침 적재·환경분석의 가격 가중합)
    b: EP당 한계비용 (프리픽스 재읽기 + δ 기록 + 산출 토큰)
    c: 컨텍스트 누적 항 (i번째 EP가 앞선 i-1개의 잔류를 지고 읽는 비용)"""
    cell = cal["cells"].get(f"{mode}/{mdl}")
    if cell is None:
        return None
    p = cal["pricing"][mdl]
    p_cr = p["input"] * cal["pricing"]["cache_read_mult"] / 1e6
    p_cw = p["input"] * cal["pricing"]["cache_write_mult"] / 1e6
    p_out = p["output"] / 1e6
    env = cell["S0"] + cell["delta_env"]
    return {"a": (p_cr * cell["tau_env"] * (cell["S0"] + cell["delta_env"] / 2)
                  + p_cw * env + p_out * cell["out_env"]),
            "b": (p_cr * cell["tau_ep"] * env + p_cw * cell["delta_ep"]
                  + p_out * cell["out_ep"]),
            "c": p_cr * cell["tau_ep"] * cell["delta_ep"] / 2}


def k_stars(cal, mode, mdl, w_soft=W_SOFT_DEFAULT):
    """설계 v2.3의 K* 병기: K*_wall(평균 w 기준, W_soft 용량이 허용하는 최대 K)과
    K*_cost(단일 청크 단가 C(K)/K가 최소인 K). 실제 파티션은 컨트롤러 경계·δ̂ 기반
    FFD라 평균 K가 이와 다를 수 있다 — 보고서 참고용 지표다.

    K*_cost는 닫힌 형태로 구한다: 균일 ŵ=1에서 simulate_chunk의 비용은
    cost(K) = A + B·K + C·K² (A: env 고정분의 가격 가중합, C = P_cr·tau_ep·delta_ep/2)
    이므로 단가 g(K) = A/K + B + C·K의 최소는 K* = √(A/C)다 — 탐색 루프가 없어
    퇴화 계수(delta_ep→0, k_wall 수만)에서도 절단·폭주가 없다."""
    cell = cal["cells"].get(f"{mode}/{mdl}")
    if cell is None:
        return None
    # delta_ep는 two_point 경로에서 최소 1로 클램프되지만, 단일 N approx 경로는 0을
    # 낼 수 있다 (모든 run의 cmax == s0) — 나눗셈 가드.
    k_wall = max(int((w_soft - cell["S0"] - cell["delta_env"])
                     // max(cell["delta_ep"], 1)), 1)
    co = cost_coefficients(cal, mode, mdl)
    if co["c"] <= 0:   # 2차 항 없음 → g 단조 감소 → 벽까지 키우는 게 최적
        return {"k_cost": k_wall, "k_wall": k_wall}
    k0 = round((co["a"] / co["c"]) ** 0.5)
    # 이산 최적은 floor/ceil 중 하나 — 후보를 simulate로 평가해 반올림 오차 방어
    cands = {min(max(k, 1), k_wall) for k in (k0 - 1, k0, k0 + 1)}
    k_cost = min(cands, key=lambda k: model.simulate_chunk(
        cal, mode, mdl, [1.0] * k)["cost_usd"] / k)
    return {"k_cost": k_cost, "k_wall": k_wall}
