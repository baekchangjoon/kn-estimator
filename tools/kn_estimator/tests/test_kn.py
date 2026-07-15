import json, os, statistics, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scan, calibrate, model, plan

# 경로 해석: ① 환경변수 오버라이드 → ② 저장소 상대 기본값 (이 파일 기준).
# tests/ → kn_estimator/ → tools/ → 저장소 루트 = parents[3].
REPO = Path(__file__).resolve().parents[3]
SUT = Path(os.environ.get("KN_SUT") or REPO / "smartplant")
LEDGER = Path(os.environ.get("KN_LEDGER") or REPO / "results/run_ledger.jsonl")
RUNS = Path(os.environ.get("KN_RUNS") or REPO / "results/runs")
SP = str(SUT)


class SkipTest(Exception):
    """건너뜀을 통과와 구분하기 위한 신호 — 조용한 green 금지.

    `optional=True`는 설계상 선택적인 스모크(외부 샘플)를 뜻한다. 필수 게이트가
    건너뛰어지면 스위트를 red로 만들지만, 선택 스모크는 그러지 않는다.
    """

    def __init__(self, reason, optional=False):
        super().__init__(reason)
        self.optional = optional


def _skip(reason, optional=False):
    try:
        import pytest
    except ImportError:
        raise SkipTest(reason, optional)
    pytest.skip(reason)


def _require_sut():
    """SUT(smartplant)는 gitignore 대상이라 부재할 수 있다 — 조용히 통과시키지 않는다."""
    if not SUT.exists():
        _skip(f"SUT 없음 ({SUT}) — KN_SUT 환경변수로 지정 가능")


# ---- scan -------------------------------------------------------------------

def test_inventory_count_matches_registered():
    _require_sut()
    eps = scan.inventory(SP)
    assert len(eps) == 167, len(eps)


def test_slice_finds_service_dao_and_mybatis_xml():
    _require_sut()
    eps = scan.inventory(SP)
    mng = next(e for e in eps if e["path"] == "/web/super/admin/mngTerms" and e["method"] == "GET")
    sl = scan.build_slices(SP, [mng])[0]
    files = "\n".join(sl["files"])
    assert "MngTermsService.java" in files, files
    assert "MngTermsDAO.java" in files, files
    assert "sql/mngTerms.xml" in files, files
    assert sl["w_tokens"] > 1000
    assert sl["unresolved"] == [] or all("DataSourceTransactionManager" in u or "." not in u
                                          for u in sl["unresolved"])


def test_controller_tokens_counted_once_per_controller_not_per_ep():
    _require_sut()
    eps = scan.inventory(SP)
    quota = [e for e in eps if e["controller"] == "QuotaController"]
    assert len(quota) >= 2
    sls = scan.build_slices(SP, quota)
    # 같은 컨트롤러의 EP들: 컨트롤러 본체 토큰은 shared_tokens로 분리, EP w에는 핸들러 span만
    for sl in sls:
        assert sl["handler_tokens"] < sl["controller_shared_tokens"], sl["endpoint"]["handler"]


# ---- calibrate --------------------------------------------------------------

def _cal():
    return calibrate.calibrate(LEDGER, RUNS)


def test_calibration_cells_present_and_versioned():
    cal = _cal()
    assert "flat/opus" in cal["cells"] and "template/sonnet" in cal["cells"]
    assert cal["pricing"]["opus"]["input"] == 5.0
    assert cal["version"]
    c = cal["cells"]["flat/opus"]
    for k in ("S0", "tau_ep", "delta_ep", "out_ep", "tau_env", "delta_env", "out_env"):
        assert k in c, k


def test_uncalibrated_cell_is_flagged():
    cal = _cal()
    # 존재하지 않는 조합은 insufficient_calibration으로 표시되어야 함
    est = model.estimate_cell(cal, "batched", "haiku", [1.0] * 8)
    assert est.get("status") == "insufficient_calibration"


# ---- model: hold-out & coverage ---------------------------------------------

def _measured(arm, n):
    rows = [json.loads(l) for l in (LEDGER).read_text().splitlines()]
    return [r["cost_usd"] for r in rows if r["variant"] == arm and r.get("n") == n
            and r["role"] == "run_total" and r.get("rep") in (1, 2, 3)]


def test_holdout_fit_partial_predict_rest_flat_opus():
    # N=1 전체 + N=8 rep1로 fit → N=8 예측이 실측 3회 [min,max] 안
    cal = calibrate.calibrate(LEDGER, RUNS,
                              include=lambda r: not (r["variant"] == "flat" and r["n"] == 8
                                                     and r["rep"] in (2, 3)))
    est = model.estimate_cell(cal, "flat", "opus", [1.0] * 8)
    lo, hi = min(_measured("flat", 8)), max(_measured("flat", 8))
    assert lo * 0.8 <= est["cost_usd"] <= hi * 1.2, (est["cost_usd"], lo, hi)


def test_order_preservation():
    cal = _cal()
    w = [1.0] * 8
    f_o = model.estimate_cell(cal, "flat", "opus", w)["cost_usd"]
    f_s = model.estimate_cell(cal, "flat", "sonnet", w)["cost_usd"]
    t_o = model.estimate_cell(cal, "template", "opus", w)["cost_usd"]
    t_s = model.estimate_cell(cal, "template", "sonnet", w)["cost_usd"]
    assert t_o < f_o and t_s < f_s, (t_o, f_o, t_s, f_s)
    assert f_o < f_s, (f_o, f_s)


def test_loo_prediction_interval_coverage():
    # 각 셀: rep 하나를 빼고 fit → 빠진 rep이 α-민감도 예측구간 안에 드는 비율 ≥ 2/3
    hits = tot = 0
    for arm, cell in (("flat", ("flat", "opus")), ("flat_template", ("template", "opus")),
                      ("flat_sonnet", ("flat", "sonnet")),
                      ("flat_template_sonnet", ("template", "sonnet"))):
        for held in (1, 2, 3):
            cal = calibrate.calibrate(
                LEDGER, RUNS,
                include=lambda r, a=arm, h=held: not (r["variant"] == a and r["n"] == 8
                                                      and r["rep"] == h))
            est = model.estimate_cell(cal, cell[0], cell[1], [1.0] * 8)
            if est.get("status"):
                continue
            actual = [json.loads(l)["cost_usd"] for l in
                      (LEDGER).read_text().splitlines()
                      if f'"run_id": "{arm}-n8-r{held}"' in l.replace('": "', '": "')
                      and '"run_total"' in l]
            actual = _measured(arm, 8)[held - 1] if not actual else actual[0]
            tot += 1
            if est["pi_low"] <= actual <= est["pi_high"]:
                hits += 1
    assert tot >= 10 and hits / tot >= 2 / 3, (hits, tot)


# ---- plan --------------------------------------------------------------------

def test_partition_covers_all_and_respects_walls():
    _require_sut()
    eps = scan.inventory(SP)
    sls = scan.build_slices(SP, eps)
    cal = _cal()
    p = plan.build_plan(sls, cal, mode="template", mdl="sonnet")
    all_eps = [f"{e['endpoint']['method']} {e['endpoint']['path']}" for c in p["chunks"]
               for e in c["endpoints"]]
    assert len(all_eps) == len(sls) and len(set(all_eps)) == len(all_eps)
    for c in p["chunks"]:
        assert c["est_peak_context"] <= p["w_hard"], (c["est_peak_context"], p["w_hard"])
    assert p["n_chunks"] >= 2  # 167개면 반드시 다청크
    assert p["total_cost_usd"] > 0


def test_smoke_external_project():
    # graph-rag 샘플: 크래시 없이 N·플랜 산출 (경로 없으면 skip)
    # 경로는 KN_EXTERNAL_SAMPLE로 지정한다. 기존 하드코딩은 타 세션 스크래치패드를
    # 가리켜 항상 SKIP됐다 (실효 커버리지 0).
    ext_env = os.environ.get("KN_EXTERNAL_SAMPLE")
    if not ext_env:
        _skip("KN_EXTERNAL_SAMPLE 미지정 — 외부 프로젝트 스모크 생략", optional=True)
    ext = Path(ext_env)
    if not ext.exists():
        _skip(f"외부 샘플 없음 ({ext})", optional=True)
    eps = scan.inventory(str(ext))
    if not eps:
        _skip(f"외부 샘플에서 엔드포인트 미검출 ({ext})", optional=True)
    sls = scan.build_slices(str(ext), eps)
    p = plan.build_plan(sls, _cal(), mode="template", mdl="sonnet")
    assert p["n_chunks"] >= 1


if __name__ == "__main__":
    passed, skipped, required_skipped = 0, 0, 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except SkipTest as e:
            skipped += 1
            required_skipped += 0 if e.optional else 1
            print(f"SKIP{'' if e.optional else ' (필수 게이트!)'} {name}: {e}")
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed} passed, {skipped} skipped ({required_skipped} required)")
    if required_skipped:
        sys.exit(1)   # 필수 게이트를 건너뛰고 green이라 주장하지 않는다
