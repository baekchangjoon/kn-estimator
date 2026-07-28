#!/usr/bin/env python3
"""kn-estimator CLI: 대상 목록 → N·w 분포·청크 플랜·비용 예측.

사용:
  kn-estimate <project_root>       [옵션]   # Spring 스캐너 앞단 (엔드포인트 자동 열거)
  kn-estimate --targets <파일|->   [옵션]   # 범용 앞단 — 대상 목록 직접 공급
  kn-estimate --n <개수>           [옵션]   # 범용 앞단 — 개수만 (균일 w)
  옵션: [--label <작업라벨>] [--model sonnet|opus|haiku] [--calibration cal.json]
        [--w-soft 180000] [--w-hard 900000] [--conservative] [--parallel]
        [--groups] [--out-dir .kn]

출력: <out-dir>/kn-report.md (사람용), <out-dir>/kn-plan.json (기계용).
LLM 호출 없음 — 파일 스캔만으로 수 초 내 동작.
"""
import argparse, json, statistics, sys
from pathlib import Path

from . import model, plan as plan_mod, scan, targets as targets_mod

HERE = Path(__file__).resolve().parent
BUNDLED_CALIBRATION = HERE / "data/calibration.json"


def load_calibration(path=None):
    """캘리브레이션 로드 — 기본은 패키지 동봉본.

    시딩 시점에는 실행마다 `results/`(23MB 트랜스크립트)를 읽어 재계산했다. 그 의존이
    도구를 저장소에 묶었다. 이제 사전 산출본을 동봉하고, 원장은 `kn-calibrate`로
    오프라인 재생성한다. 부재·파손 시 조용히 폴백하지 않고 재생성 방법을 알린다.
    """
    if path:
        p = Path(path)
        # 동봉 번들 이름(petclinic·community·auth-user)도 허용 — 파일 경로가 아니면
        # data/calibration-<이름>.json (기본 번들 이름이면 calibration.json)을 쓴다.
        if not p.exists() and "/" not in str(path):
            bundled = (BUNDLED_CALIBRATION if path == "auth-user"
                       else HERE / f"data/calibration-{path}.json")
            if bundled.exists():
                p = bundled
            else:
                raise SystemExit(
                    f"'{path}'는 파일도 동봉 번들 이름도 아니다 — 동봉 번들: "
                    "auth-user(기본), petclinic, community. 또는 캘리브레이션 파일 경로를 지정하라.")
        if not p.exists():
            raise SystemExit(f"--calibration 경로를 찾을 수 없다: {p}")
    else:
        p = BUNDLED_CALIBRATION
        if not p.exists():
            # 동봉본이 없다 = 설치가 깨졌다. 원장이 있는 저장소에서만 재생성 가능하다.
            raise SystemExit(
                f"동봉 캘리브레이션이 없다: {p}\n"
                "설치가 손상됐을 수 있다. 저장소에서 재생성:\n"
                "  kn-calibrate --ledger results/run_ledger.jsonl --runs results/runs "
                f"--out {p}")
    try:
        cal = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"캘리브레이션 파싱 실패: {p}\n{e}")
    if not isinstance(cal, dict) or not cal.get("cells"):
        raise SystemExit(f"캘리브레이션에 셀이 없다: {p}")
    return cal


def _display(s):
    """슬라이스 표시 문자열 — 스캐너는 "METHOD /path", 범용 앞단은 id 그대로.
    리포트·--groups·plan.json이 공유한다 (method가 비면 선행 공백 금지)."""
    e = s["endpoint"]
    return f"{e['method']} {e['path']}" if e["method"] else e["path"]


# 한계 고지에서 스캐너/범용 분기가 공유하는 불릿 — 축자 중복이면 문구 수정 시
# 한쪽만 고치는 드리프트가 난다 (2026-07-28 리뷰).
_LIM_VARIANCE = ("- 캘리브레이션 셀별 실측 run 분산(±30~46%)이 예측 하한 오차 — "
                 "구간으로 해석할 것.")
_LIM_W_RELATIVE = ("- w는 상대 공변량으로만 쓰인다 — 절대 비용 수준은 전적으로 "
                   "캘리브레이션 계수에서 온다 (w를 일괄 배수해도 결과는 불변).")
_LIM_UNCAL = "- 미캘리브레이션 셀은 insufficient_calibration으로 표기 (추정치 미제공)."


def _plan_interval(cal, label, mdl, slices, p, w_soft, parallel=False):
    """플랜 총액의 예측구간 (low, high).

    α 민감도는 **선택된 파티션 위에서** 재시뮬레이션해 구한다. `estimate_cell`의 비율을
    가져다 쓰면 안 된다 — 그건 167개 EP를 한 청크로 보는 가정이라 peak_context가 w_hard를
    3배 넘는, 플랜이 스스로 거부할 구성이고 비용 구성비도 실제 파티션과 다르다.

    거기에 run 분산 밴드(실측 ±30~46%)를 곱한다. 이 구간이 없으면 보고서가 센트 단위
    점추정만 보여줘 거짓 정밀도를 준다.
    """
    if p.get("status") or not p.get("chunks"):
        return None
    w_mean = sum(s["w_tokens"] for s in slices) / len(slices)
    totals = {}
    for alpha in (0.0, 1.0):
        total = 0.0
        for c in p["chunks"]:
            whs = [s["w_tokens"] / w_mean for s in c["endpoints"]]
            sim = model.simulate_chunk(cal, label, mdl, whs, alpha=alpha)
            if sim.get("status"):
                return None
            # build_plan과 동일한 누적 규칙 (soft 초과 패널티 포함)
            total += sim["cost_usd"] * (1.15 if sim["peak_context"] > w_soft else 1.0)
        if parallel:   # build_plan과 동일한 병렬 할증 — 총액과 구간의 실행 가정 일치
            total *= 1.05
        totals[alpha] = total
    base = p["total_cost_usd"]
    lo_a, hi_a = min(totals.values()), max(totals.values())
    b_lo, b_hi = model._run_variance_band(cal)
    return min(lo_a, base) * b_lo, max(hi_a, base) * b_hi


def _env_wall_warning(cal, label, mdl, w_soft, noun_short="EP"):
    """환경 고정분이 W_soft를 사실상 채우면 경고 문자열, 아니면 None.

    S0+delta_env가 벽의 90%를 넘으면 EP를 담을 여유가 없어 파티션이 EP당 1청크로
    퇴화하고 고정비를 N번 물게 된다 — 다중 프로젝트 캠페인(2026-07-17)에서 자체
    캘리브레이션 예측이 실측의 3~6배로 부풀던 원인. W_soft는 캘리브레이션에서
    유도되지 않는 CLI 기본값이라, 새 캘리브레이션을 물릴 때는 게이트 통과 세션의
    컨텍스트 분포로 재산정해야 한다.
    """
    c = cal["cells"].get(f"{label}/{mdl}")
    if not c:
        return None
    env = c.get("S0", 0) + c.get("delta_env", 0)
    if env >= 0.9 * w_soft:
        return (f"경고: 환경 고정분 S0+delta_env={env:,.0f}이 W_soft={w_soft:,}의 90%를 "
                f"넘습니다. 파티션이 {noun_short}당 1청크로 퇴화해 비용이 과대추정될 수 "
                f"있습니다 — 게이트 통과 세션의 실측 컨텍스트 분포로 --w-soft 재산정을 "
                f"권장합니다.")
    return None


def build_matrix(sls, cal, w_hard, w_soft, parallel=False):   # 인자 순서 = build_plan
    """셀(라벨×모델) 매트릭스 — 캘리브레이션이 **보유한 셀**과 스킵된 셀(사유 병기)로
    구성한다. 라벨이 자유 문자열이라 고정 그리드를 열거할 수 없다.

    권장 플랜과 같은 실행 가정(parallel 포함)으로 계산한다 — 전달하지 않으면
    권장 플랜(1.05× 할증)과 매트릭스의 동일 셀 총액이 어긋난다 (2026-07-26 감사 #7)."""
    matrix = {}
    for key in sorted(set(cal["cells"]) | set(cal.get("skipped_cells") or {})):
        if key not in cal["cells"]:
            matrix[key] = f"insufficient_calibration ({cal['skipped_cells'][key]})"
            continue
        label, mdl = key.rsplit("/", 1)   # model은 뒤쪽 고정 — 방어적 파싱
        pm = plan_mod.build_plan(sls, cal, label=label, mdl=mdl,
                                 w_hard=w_hard, w_soft=w_soft, parallel=parallel)
        if pm.get("status"):
            # 선택 셀이 아니어도 벽을 못 맞추는 셀이 있을 수 있다. 크래시 대신 표기.
            matrix[key] = pm["status"]
            continue
        matrix[key] = {"total_cost_usd": pm["total_cost_usd"],
                       "n_chunks": pm["n_chunks"], "k_avg": pm["k_avg"],
                       "wall_h": round(pm["total_wall_s"] / 3600, 1)}
    return matrix


def main():
    ap = argparse.ArgumentParser(
        description="대상 목록(스캐너 자동 또는 --targets/--n) → 테스트·분석 등 "
                    "반복 작업의 비용/청크 플랜 예측 (LLM 호출 없음)")
    ap.add_argument("project_root", nargs="?",
                    help="스캔할 Spring 프로젝트 루트 디렉토리 (생략 시 --targets/--n 필요)")
    ap.add_argument("--targets",
                    help="대상 목록 파일 또는 '-'(stdin, 텍스트 전용). 한 줄에 대상 "
                         "하나; .json 확장자면 [{id, w?, group?}] 정밀형")
    ap.add_argument("--n", type=int,
                    help="목록 없이 개수만 — 균일 w의 합성 대상 N개")
    ap.add_argument("--label", default="template",
                    help="작업 라벨 — 캘리브레이션 셀 이름의 앞 절반 (자유 문자열; "
                         "동봉 캘리브레이션의 라벨: template|flat)")
    ap.add_argument("--model", default="sonnet", choices=["sonnet", "opus", "haiku"],
                    help="대상 모델 (기본 sonnet; 미캘리브레이션 셀은 수치 미제공)")
    ap.add_argument("--calibration",
                    help="캘리브레이션 파일 경로 또는 동봉 번들 이름 "
                         "(auth-user[기본]|petclinic|community)")
    ap.add_argument("--w-soft", type=int, default=plan_mod.W_SOFT_DEFAULT,
                    help=f"품질 정책 벽 (기본 {plan_mod.W_SOFT_DEFAULT:,}; 유효 W_hard로 캡)")
    ap.add_argument("--w-hard", type=int, default=plan_mod.W_HARD_DEFAULT,
                    help=f"모델 상한 벽 (기본 {plan_mod.W_HARD_DEFAULT:,}; 모델 윈도우×0.9로 캡)")
    ap.add_argument("--conservative", action="store_true", help="W_soft=250K 보수 프리셋")
    ap.add_argument("--parallel", action="store_true",
                    help="청크 병렬 실행 가정 (벽시계=max, cache_write 5% 할증)")
    ap.add_argument("--groups", action="store_true",
                    help="비용 최적 생성 묶음을 '그룹N(대상, …)' 형태로 출력")
    ap.add_argument("--out-dir", default=".kn",
                    help="산출물 디렉토리 (기본 .kn — 프로젝트 루트/cwd 기준 상대 또는 절대 경로)")
    args = ap.parse_args()
    sources = [x for x in (args.project_root, args.targets, args.n) if x is not None]
    if len(sources) != 1:
        raise SystemExit("입력 소스는 정확히 하나여야 한다 — <project_root> | "
                         "--targets <파일|-> | --n <개수> 중 하나를 지정하라.")
    if args.n is not None and args.n <= 0:
        raise SystemExit(f"--n 은 양수여야 한다 (받은 값: {args.n})")
    if args.w_soft <= 0 or args.w_hard <= 0:
        raise SystemExit("--w-soft/--w-hard 는 양수여야 한다 "
                         f"(받은 값: w_soft={args.w_soft}, w_hard={args.w_hard})")
    if "/" in args.label:
        raise SystemExit(f"라벨에 '/'를 쓸 수 없다 ('{args.label}') — "
                         "셀 키가 <label>/<model> 형식이라 구분자와 충돌한다.")
    w_soft = 250_000 if args.conservative else args.w_soft

    cal = load_calibration(args.calibration)

    generic = args.project_root is None
    noun = "대상" if generic else "엔드포인트"
    noun_short = "대상" if generic else "EP"
    if generic:
        # 배타 검증과 같은 술어(is not None) — truthiness로 고르면 --targets ""가
        # n_targets(None)로 새어 TypeError가 난다.
        meta = (targets_mod.parse_targets(args.targets) if args.targets is not None
                else targets_mod.n_targets(args.n))
        sls, n = meta["slices"], meta["n"]
        w_source, src_label = meta["w_source"], meta["source_label"]
        unresolved = 0
    else:
        root = Path(args.project_root)
        if not root.is_dir():
            raise SystemExit(f"프로젝트 경로가 없거나 디렉토리가 아니다: {root}")
        eps = scan.inventory(args.project_root)
        if not eps:
            print("No JSON endpoints found."); sys.exit(1)
        sls = scan.build_slices(args.project_root, eps)
        n = len(sls)
        w_source, src_label = "file", args.project_root
        unresolved = sum(1 for s in sls if s["unresolved"])
    ws = sorted(s["w_tokens"] for s in sls)
    tertiles = ws[n // 3], ws[2 * n // 3]
    uniform_w = generic and w_source == "uniform"
    if n > 5_000:
        # FFD×W_target 그리드×매트릭스 셀 반복이 준2차라 N=3만에 ~20초, 10만이면
        # 수 분 — 침묵 대신 예고한다 (실측 2026-07-28 리뷰).
        print(f"경고: N={n:,} — 대상이 많아 플랜 탐색이 수 분 걸릴 수 있습니다.",
              file=sys.stderr)

    # 선택 셀의 유효 벽 = build_plan이 실제로 쓰는 값 (모델 윈도우 캡 반영).
    # 경고·K*·보고서가 이 값을 공유해야 한다 — 요청값을 그대로 쓰면 haiku에
    # soft 벽이 hard 벽보다 큰 자기모순 보고서가 나온다.
    eff_soft = min(w_soft, args.w_hard, plan_mod.model_w_hard(args.model))

    warn = _env_wall_warning(cal, args.label, args.model, eff_soft, noun_short)
    if warn:
        print(warn)

    p = plan_mod.build_plan(sls, cal, label=args.label, mdl=args.model,
                            w_hard=args.w_hard, w_soft=w_soft, parallel=args.parallel)
    if p.get("status"):
        why = (cal.get("skipped_cells") or {}).get(f"{args.label}/{args.model}")
        if p["status"] == "insufficient_calibration" and not why:
            # 원장에 등장하지 않던 셀 — 스킵 사유조차 없다. 빈 메시지로 죽지
            # 않고 미측정 사실과 가용 셀을 알린다.
            why = (f"동봉 캘리브레이션 미측정 셀 — 가용: {', '.join(sorted(cal['cells']))}")
        print(f"{p['status']}: {p.get('reason') or why or ''}")
        sys.exit(1)

    interval = _plan_interval(cal, args.label, args.model, sls, p, p["w_soft"],
                              parallel=args.parallel)
    k_star = plan_mod.k_stars(cal, args.label, args.model, p["w_soft"])
    curve = plan_mod.cost_coefficients(cal, args.label, args.model)

    # 공통 이상치 감지 (모든 앞단): 강제 단독 배치 없이 경고·권고만 — 고정비 중복
    # 역효과가 있고 δ̂-FFD가 초과 항목을 자연 격리한다. '파일럿' 단어 금지
    # (test_pilot_notice의 "--calibration 명시 시 '파일럿' 없음" 불변식과 충돌).
    outlier_msg = None
    outs = targets_mod.outliers(sls)
    if outs:
        med = statistics.median(s["w_tokens"] for s in sls)
        listing = ", ".join(
            f"{_display(s)} (w={s['w_tokens']:,.0f}, {s['w_tokens'] / med:.0f}배)"
            for s in outs[:5])
        more = f" 외 {len(outs) - 5}건" if len(outs) > 5 else ""
        outlier_msg = (f"⚠ 이상치 {len(outs)}건: {listing}{more} — 이 {noun}들은 "
                       "나머지와 크기가 이질적입니다 — 현재 셀 계수로의 외삽은 "
                       "과소추정 위험이 있어 별도 라벨로 분리 측정을 권장합니다.")

    # 단위(그룹) 집계: 스캐너 = 컨트롤러, 범용 = 명시 group만 (합성 무그룹 키는
    # 사용자 어휘가 아니다 — 어떤 산출물에도 노출 금지). 단위별 a,b,c는 만들지
    # 않는다 — 컨트롤러 소속의 분산 설명력 검정(research/unit_variance.py)에서
    # 유의한 근거가 없었다 (전 케이스 순열 p≥0.079, 절반은 구조적으로 검정 불가).
    controllers = {}
    for i, c in enumerate(p["chunks"]):
        for s in c["endpoints"]:
            name = s["endpoint"]["controller"]
            if generic and not targets_mod.is_explicit_group(name):
                continue
            info = controllers.setdefault(name, {"n": 0, "w_tokens": 0, "chunks": []})
            info["n"] += 1
            info["w_tokens"] += s["w_tokens"]
            if i not in info["chunks"]:
                info["chunks"].append(i)

    matrix = build_matrix(sls, cal, args.w_hard, w_soft, args.parallel)

    out = (Path.cwd() if generic else Path(args.project_root)) / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    plan_json = {**{k: v for k, v in p.items() if k != "chunks"},
                 "chunks": [{**c, "endpoints": [_display(s) for s in c["endpoints"]]}
                            for c in p["chunks"]],
                 "k_star": k_star,
                 "cost_curve": curve,
                 "controllers": controllers,
                 "calibration_version": cal["version"]}
    (out / "kn-plan.json").write_text(json.dumps(plan_json, indent=2, ensure_ascii=False))

    if generic:
        src_disp = (f"({src_label}, {n}건)" if src_label == "stdin"
                    else f"({src_label})" if src_label.startswith("--n ")
                    else f"`{src_label}`")
        lines = [
            "# kn-estimator 보고서", "",
            "> **절대 USD는 비보증** — 캘리브레이션은 단일 프로젝트(tainted-spring-auth-user) 실측 기반이며,",
            "> 이 보고서의 주 용도는 라벨·모델·청크 구성의 **상대 비교**다.", "",
            f"- 대상: {src_disp}",
            f"- **N = {n}** {noun}"]
        if uniform_w:
            note = meta.get("uniform_note")
            lines.append(f"- w: 균일 가정{f' ({note})' if note else ''}")
        else:
            lines.append(f"- w 분포: p33={tertiles[0]:,.0f} / p66={tertiles[1]:,.0f} "
                         f"/ max={ws[-1]:,.0f} tokens (상대 비교용)")
    else:
        lines = [
            "# kn-estimator 보고서", "",
            "> **절대 USD는 비보증** — 캘리브레이션은 단일 프로젝트(tainted-spring-auth-user) 실측 기반이며,",
            "> 이 보고서의 주 용도는 라벨·모델·청크 구성의 **상대 비교**다. 토큰 추정은 bytes/4",
            "> 근사(fallback)를 사용한다.", "",
            f"- 대상: `{args.project_root}`",
            f"- **N = {n}** 엔드포인트 (미해결 슬라이스 {unresolved}건 = {unresolved/n:.0%} — 중앙값 prior 적용)",
            f"- w 분포: p33={tertiles[0]:,} / p66={tertiles[1]:,} / max={ws[-1]:,} tokens (상대 비교용)"]
    if outlier_msg:
        lines.append(f"> {outlier_msg}")
    lines += ["",
        f"## 권장 플랜 ({args.label}×{args.model})", "",
        f"- 청크 수: **{p['n_chunks']}** (평균 K={p['k_avg']})",
        f"- 예상 총비용: **${p['total_cost_usd']}** / 예상 벽시계: {p['total_wall_s']/3600:.1f}h"
        f" ({'병렬' if args.parallel else '순차'})",
        (f"- **예측구간: ${interval[0]:,.0f} ~ ${interval[1]:,.0f}**"
         " — 이 파티션의 α 민감도 × 실측 run 분산. 점추정보다 이 구간으로 해석할 것."
         if interval else "- 예측구간: 산출 불가 (캘리브레이션 부족)"),
        f"- 벽: W_soft={p['w_soft']:,} (품질 정책), W_hard={p['w_hard']:,} (모델 상한 반영)",
        (f"- K*_cost={k_star['k_cost']} (셀 단가 최소 K), K*_wall={k_star['k_wall']}"
         f" (W_soft 용량 상한, 평균 w 기준) — 실제 파티션은 "
         f"{'그룹' if generic else '컨트롤러'} 경계·δ̂ 기반이라"
         " 평균 K와 다를 수 있다" if k_star else "- K*: 산출 불가 (캘리브레이션 부족)"),
        (f"- 비용 곡선(셀 합성, 단일 청크·평균 w 기준, USD): "
         f"C(K) ≈ {curve['a']:.2f} + {curve['b']:.3f}·K + {curve['c']:.4f}·K²"
         f" — a: 청크 고정비, b: {noun_short} 한계비용, c: 컨텍스트 누적 항"
         f" (무제약 K*=√(a/c)≈{(curve['a'] / curve['c']) ** 0.5:.1f}"
         " — 위 K*_cost는 K*_wall 절단 반영)"
         if curve and curve["c"] > 0 else "- 비용 곡선: 산출 불가 (캘리브레이션 부족)"),
        f"- soft 초과 청크: {sum(1 for c in p['chunks'] if c['soft_exceeded'])}건"
        + (f" (요청 W_soft={w_soft:,} → 유효값으로 캡됨)" if p["w_soft"] < w_soft else ""), "",
        "## 셀(라벨×모델) 매트릭스 (동일 플랜 로직)", "",
        "| 셀 | 총비용 | 청크 수 | 평균 K | 벽시계 |", "|---|---|---|---|---|"]
    for key, v in matrix.items():
        if isinstance(v, str):
            lines.append(f"| {key} | {v} | — | — | — |")
        else:
            lines.append(f"| {key} | ${v['total_cost_usd']} | {v['n_chunks']} | {v['k_avg']} | {v['wall_h']}h |")
    if any(isinstance(v, dict) for k, v in matrix.items() if k.endswith("/haiku")):
        lines += ["",
                  "> ⚠ haiku 최저가에는 **검증 통과율 리스크**가 따른다 — 캠페인 실측에서"
                  " 소형 프로젝트 게이트 전멸 사례(petclinic template/haiku 0/6)가 있었다."
                  " 금액만으로 선택하지 말 것 (docs/GUIDE.md §4.3)."]
    if generic:
        if controllers:
            lines += ["", "## 그룹 단위", "",
                      "| 그룹 | n | Σw (tokens) | 배정 청크 |", "|---|---|---|---|"]
            for name in sorted(controllers, key=lambda c: -controllers[c]["n"]):
                info = controllers[name]
                lines.append(f"| {name} | {info['n']} | {info['w_tokens']:,.0f} "
                             f"| {', '.join(f'#{i}' for i in info['chunks'])} |")
        if not uniform_w:
            top = sorted(sls, key=lambda s: -s["w_tokens"])[:10]
            w_note = ("> w는 파일 크기(bytes/4)다 — 분기 수 등 복잡도는 반영하지 않는다."
                      if w_source == "file" else
                      "> w는 사용자 제공값이다 — 상대 비교로만 쓰인다.")
            lines += ["", f"## w 상위 10 {noun}", "", w_note, "",
                      "| 대상 | w (tokens) |", "|---|---|"]
            for s in top:
                lines.append(f"| {_display(s)} | {s['w_tokens']:,.0f} |")
        w_limit = {"file": "- w는 파일 크기(bytes/4)만 반영하고 복잡도(분기 수 등)는 미반영.",
                   "json": "- w는 사용자 제공값이다 — 검증 없이 상대 비교에만 쓰인다.",
                   "uniform": "- w는 균일 가정이다 — 상대 배분에 영향 없음."}[w_source]
        lines += ["", "## 한계 고지", "",
                  _LIM_VARIANCE,
                  w_limit,
                  _LIM_W_RELATIVE,
                  f"- 캘리브레이션 버전: {cal['version']} (N=8 관측 기반 — 대규모 N 외삽 미검증).",
                  "- 캘리브레이션의 n과 이 목록의 N은 **같은 단위**로 세어져야 한다"
                  " (단위 일관성 계약 — docs/CONCEPTS.md).",
                  _LIM_UNCAL]
    else:
        lines += ["", "## 컨트롤러 단위", "",
                  "> n·Σw·배정 청크는 컨트롤러별로 산출하지만, 비용 계수(a,b,c)는 셀 전역"
                  " 하나다 — 컨트롤러 소속의 분산 설명력 검정(research/unit_variance.py)에서"
                  " 실질 검정 가능한 케이스(petclinic 2건)가 유의하지 않았고(p=0.079, 0.341),"
                  " 나머지는 표본 구조상 검정 불가·검정력 없음이었다.", "",
                  "| 컨트롤러 | n (EP) | Σw (tokens) | 배정 청크 |", "|---|---|---|---|"]
        for name in sorted(controllers, key=lambda c: -controllers[c]["n"]):
            info = controllers[name]
            lines.append(f"| {name} | {info['n']} | {info['w_tokens']:,} "
                         f"| {', '.join(f'#{i}' for i in info['chunks'])} |")
        top = sorted(sls, key=lambda s: -s["w_tokens"])[:10]
        lines += ["", "## 슬라이스 크기 상위 10 엔드포인트", "",
                  "> w는 **코드 크기**(bytes/4)다 — 분기 수 등 복잡도는 반영하지 않는다.", "",
                  "| Endpoint | w (tokens) | external | unresolved |", "|---|---|---|---|"]
        for s in top:
            e = s["endpoint"]
            lines.append(f"| {e['method']} {e['path']} | {s['w_tokens']:,} "
                         f"| {'Y' if s['external_call'] else ''} | {', '.join(s['unresolved'])} |")
        lines += ["", "## 한계 고지", "",
                  _LIM_VARIANCE,
                  "- **작업량 w는 코드 크기만 반영하고 복잡도(분기 수·순환복잡도)는 미반영.** 같은"
                  " 크기라도 분기가 많은 핸들러는 테스트가 더 필요하나 동일하게 취급된다.",
                  _LIM_W_RELATIVE,
                  "- 정적 슬라이스는 리플렉션·동적 라우팅·설정 기반 빈을 과소평가할 수 있음.",
                  f"- 캘리브레이션 버전: {cal['version']} (N=8 관측 기반 — 대규모 N 외삽 미검증).",
                  _LIM_UNCAL]
    (out / "kn-report.md").write_text("\n".join(lines) + "\n")
    print(f"N={n} chunks={p['n_chunks']} k_avg={p['k_avg']} est=${p['total_cost_usd']}")
    if outlier_msg:
        # stdout 위치 계약: N=… 요약 직후, --groups 블록·파일럿 고지(ℹ)보다 앞.
        print(outlier_msg)
    if args.groups:
        # "그룹1(q, w, e), 그룹2(z, x, y)로 돌리세요" — 요청 한 줄에 실행 계획으로
        # 답하기 위한 출력. 각 그룹 = 독립 세션 1개 (이 조건이 1차 비용의 전제다).
        # 헤더는 소스 라벨의 짧은 형태 — 목록 파일은 stem (스캐너의 project.name과
        # 같은 급), stdin·--n은 라벨 그대로 (design §3).
        hdr = ((src_label if src_label == "stdin" or src_label.startswith("--n ")
                else Path(src_label).stem) if generic
               else Path(args.project_root).resolve().name)
        print(f"\n[{hdr}] 비용 최적 생성 묶음 ({args.label}×{args.model}):")
        for i, c in enumerate(p["chunks"], 1):
            ep_labels = ", ".join(_display(s) for s in c["endpoints"])
            # 그룹 비용은 soft 페널티 반영값 — 총액과의 합산 정합을 위해서다
            # (병렬 할증 5%는 플랜 수준 근사라 총액에만 명시).
            shown = round(c["est_cost_usd"] * (1.15 if c["soft_exceeded"] else 1.0), 2)
            mark = " ⚠soft 초과" if c["soft_exceeded"] else ""
            print(f"  그룹{i}({ep_labels}) — ${shown}, peak {c['est_peak_context']:,}{mark}")
        many = p["n_chunks"] > 1
        print((f"위 {p['n_chunks']}개 그룹을 각각 **새 독립 세션**으로 돌리세요"
               if many else "위 그룹을 **새 독립 세션**으로 돌리세요")
              + " — 세션을 이어가면 비용이 2차로 돌아갑니다. "
              f"예상 총 ${p['total_cost_usd']}"
              + (" (병렬 cache_write 할증 5% 포함)" if args.parallel else "")
              + (f", 예측구간 ${interval[0]:,.0f}~${interval[1]:,.0f}" if interval else "")
              + ".")
    if not args.calibration:
        # 자체 캘리브레이션 없이 = 동봉(tainted-spring-auth-user) 계수로 돌린 실행. 캘리브레이션은
        # 실측 run 원장이 필요해 도구가 자동 수행할 수 없다 — 고지가 자동화의 상한.
        # 처방은 실행 가능해야 한다: kn-calibrate의 env/ep 2점 분해에는 같은 셀에서
        # **크기가 다른** run 2개 이상이 필요하다 (그룹 1개면 insufficient_runs 또는
        # 기준 셀 부재로 셀이 산출되지 않는다). --groups 출력 뒤에 인쇄한다 — "플랜의
        # 그룹"이 이미 인쇄된 것을 가리키게.
        print("ℹ 이 프로젝트의 자체 캘리브레이션이 없습니다 — 동봉(tainted-spring-auth-user) 계수로 "
              "추정했습니다 (상대 비교용, 절대 금액 비보증).\n"
              "  금액 정확도가 필요하면 파일럿 캘리브레이션부터: 같은 라벨×모델로 "
              f"크기가 다른 그룹 2개 이상 실측(예: {noun_short} 1개짜리 + 최소 그룹) → "
              "kn-calibrate --ledger <원장> --runs <런들> --out my-cal.json → "
              "게이트 통과 세션의 컨텍스트 분포로 --w-soft 재산정 → "
              "--calibration my-cal.json 으로 재실행. 캠페인 설계(N 2점×반복 3) "
              "실측에서 오차 −34% → ±10%. 원장 스키마·절차: docs/GUIDE.md §4.4")
    print(f"report: {out/'kn-report.md'}\nplan:   {out/'kn-plan.json'}")


if __name__ == "__main__":
    main()
