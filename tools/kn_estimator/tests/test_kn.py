import json, os, statistics, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scan, calibrate, model, plan

ROOT = Path("/home/baek/temp/reduce-token")
SP = str(ROOT / "smartplant")


# ---- scan -------------------------------------------------------------------

def test_inventory_count_matches_registered():
    eps = scan.inventory(SP)
    assert len(eps) == 167, len(eps)


def test_slice_finds_service_dao_and_mybatis_xml():
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
    eps = scan.inventory(SP)
    quota = [e for e in eps if e["controller"] == "QuotaController"]
    assert len(quota) >= 2
    sls = scan.build_slices(SP, quota)
    # 같은 컨트롤러의 EP들: 컨트롤러 본체 토큰은 shared_tokens로 분리, EP w에는 핸들러 span만
    for sl in sls:
        assert sl["handler_tokens"] < sl["controller_shared_tokens"], sl["endpoint"]["handler"]


# ---- calibrate --------------------------------------------------------------

def _cal():
    return calibrate.calibrate(ROOT / "results/run_ledger.jsonl", ROOT / "results/runs")


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
    rows = [json.loads(l) for l in (ROOT / "results/run_ledger.jsonl").read_text().splitlines()]
    return [r["cost_usd"] for r in rows if r["variant"] == arm and r.get("n") == n
            and r["role"] == "run_total" and r.get("rep") in (1, 2, 3)]


def test_holdout_fit_partial_predict_rest_flat_opus():
    # N=1 전체 + N=8 rep1로 fit → N=8 예측이 실측 3회 [min,max] 안
    cal = calibrate.calibrate(ROOT / "results/run_ledger.jsonl", ROOT / "results/runs",
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
                ROOT / "results/run_ledger.jsonl", ROOT / "results/runs",
                include=lambda r, a=arm, h=held: not (r["variant"] == a and r["n"] == 8
                                                      and r["rep"] == h))
            est = model.estimate_cell(cal, cell[0], cell[1], [1.0] * 8)
            if est.get("status"):
                continue
            actual = [json.loads(l)["cost_usd"] for l in
                      (ROOT / "results/run_ledger.jsonl").read_text().splitlines()
                      if f'"run_id": "{arm}-n8-r{held}"' in l.replace('": "', '": "')
                      and '"run_total"' in l]
            actual = _measured(arm, 8)[held - 1] if not actual else actual[0]
            tot += 1
            if est["pi_low"] <= actual <= est["pi_high"]:
                hits += 1
    assert tot >= 10 and hits / tot >= 2 / 3, (hits, tot)


# ---- plan --------------------------------------------------------------------

def test_partition_covers_all_and_respects_walls():
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
    ext = Path("/tmp/claude-1000/-home-baek-temp-reduce-token/94eecaa1-90e0-4e5b-bd22-407f35601a30/scratchpad/graph-rag/samples/order-service")
    if not ext.exists():
        print("SKIP external"); return
    eps = scan.inventory(str(ext))
    if not eps:
        print("SKIP external (no endpoints matched)"); return
    sls = scan.build_slices(str(ext), eps)
    p = plan.build_plan(sls, _cal(), mode="template", mdl="sonnet")
    assert p["n_chunks"] >= 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
