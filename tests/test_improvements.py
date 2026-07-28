"""B단계 개선 회귀 테스트 — 착수 전 red로 결함을 고정한다.

다루는 결함:
- K4: 모든 W_target frac이 벽을 넘으면 `best is None`으로 크래시.
- external_call: EXTERNAL_CALL_TYPES가 SPRING_INFRA의 부분집합이라 도달 불가 → 항상 False.
- K2(a): 예측구간 로직(estimate_cell)이 있는데 보고서가 점추정만 낸다.
- C3/C5: 보고서가 크기 순위를 "복잡도"라 라벨하고, 복잡도 미반영을 고지하지 않는다.
"""
import json
import re
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from kn_estimator import cli, model, plan, scan

REPO = Path(__file__).resolve().parents[1]
SUT = Path(os.environ.get("KN_SUT") or REPO / "petclinic")


class SkipTest(unittest.SkipTest):
    pass


def _skip(reason):
    raise SkipTest(reason)


def _require_sut():
    if not SUT.exists():
        _skip(f"SUT 없음 ({SUT}) — KN_SUT 환경변수로 지정 가능")


def _cal():
    return cli.load_calibration()


# ---- K4: 벽을 만족하는 파티션이 없을 때 -------------------------------------

def test_build_plan_reports_infeasible_instead_of_crashing():
    """모든 W_target frac이 w_hard를 넘으면 크래시가 아니라 상태를 돌려줘야 한다."""
    _require_sut()
    sls = scan.build_slices(str(SUT), scan.inventory(str(SUT)))
    got = plan.build_plan(sls, _cal(), label="template", mdl="sonnet", w_hard=1000)
    assert isinstance(got, dict), type(got)
    assert got.get("status") == "infeasible_w_hard", got.get("status")
    assert "w_hard" in got


def test_build_plan_still_works_at_realistic_walls():
    """정상 경로는 그대로여야 한다 (K4 수정이 회귀를 만들지 않았는지).

    n_chunks=3은 petclinic(N=18) × 캠페인(auth-user) 계수 × W_soft 330K 기준선이다.
    """
    _require_sut()
    sls = scan.build_slices(str(SUT), scan.inventory(str(SUT)))
    got = plan.build_plan(sls, _cal(), label="template", mdl="sonnet")
    assert got["n_chunks"] == 3, got["n_chunks"]
    assert got["total_cost_usd"] > 0


# ---- external_call: 조용한 항상-False ---------------------------------------

def test_external_call_types_are_reachable():
    """RestTemplate/WebClient가 주입 타입 필터를 통과해야 한다.

    EXTERNAL_CALL_TYPES ⊆ SPRING_INFRA 이면 _injected_types가 먼저 걸러내
    external_call 분기가 도달 불가가 되고, 플래그가 조용히 항상 False가 된다.
    """
    got = scan._injected_types("private final RestTemplate restTemplate;")
    assert "RestTemplate" in got, got


def test_external_call_is_detected_on_a_controller_that_uses_resttemplate():
    """RestTemplate을 주입받는 컨트롤러는 external_call=True여야 한다."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        java = root / "src/main/java/app"
        java.mkdir(parents=True)
        (java / "ExtController.java").write_text(textwrap.dedent("""
            package app;
            @RestController
            @RequestMapping("/api")
            public class ExtController {
                @Autowired private RestTemplate restTemplate;
                @GetMapping("/call")
                public String call() { return "x"; }
            }
        """))
        eps = scan.inventory(str(root))
        assert len(eps) == 1, eps
        sl = scan.build_slices(str(root), eps)[0]
        assert sl["external_call"] is True, sl


def test_external_call_adds_no_dependency_tokens():
    """외부 타입은 플래그만 세우고 토큰을 더하지 않는다.

    이것이 보장돼야 external_call 수정이 w_tokens·비용을 건드리지 않는다. `w == handler`면
    의존 그래프에서 가산된 토큰이 0이라는 뜻이다 (RestTemplate은 해석 대상 파일이 아니다).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        java = root / "src/main/java/app"
        java.mkdir(parents=True)
        (java / "ExtController.java").write_text(textwrap.dedent("""
            package app;
            @RestController
            @RequestMapping("/api")
            public class ExtController {
                @Autowired
                private RestTemplate restTemplate;
                @GetMapping("/call")
                public String call() { return "x"; }
            }
        """))
        sl = scan.build_slices(str(root), scan.inventory(str(root)))[0]
        assert sl["external_call"] is True, sl
        assert sl["w_tokens"] == sl["handler_tokens"], (sl["w_tokens"], sl["handler_tokens"])


# ---- K3(3): MyBatis 네임스페이스 조인 ----------------------------------------

def test_mybatis_joins_by_namespace_not_only_by_directory():
    """XML이 DAO와 패키지 병치가 아니어도 네임스페이스로 찾아야 한다.

    현재는 디렉토리 prefix 매칭만 해서, XML을 공용 디렉토리에 모아두는 프로젝트에서는
    아무것도 못 찾는다. 병치 프로젝트는 우연히 동작할 뿐이다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        java = root / "src/main/java/app/dao"
        java.mkdir(parents=True)
        # XML은 DAO 패키지와 **다른** 경로에 둔다 (병치 아님)
        xml = root / "src/main/resources/sqlmap"
        xml.mkdir(parents=True)
        (java / "OrderDAO.java").write_text(textwrap.dedent("""
            package app.dao;
            public class OrderDAO {
                public Object find(Object p) { return selectOne("orderNs.find", p); }
            }
        """))
        (xml / "order-sql.xml").write_text(
            '<mapper namespace="orderNs">\n' + "  <!-- pad -->\n" * 100 + "</mapper>\n")
        idx = scan._Index(root)
        got = [p.name for p in idx.mybatis_xml_for(java / "OrderDAO.java")]
        assert "order-sql.xml" in got, got


def test_mybatis_colocated_xml_fallback_still_works():
    """네임스페이스로 못 찾는 병치 프로젝트에서 기존(디렉토리 prefix) 결과를 잃지
    않아야 한다 — 합성 픽스처로 고정 (네임스페이스 불일치 + 패키지 병치 XML)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        java = root / "src/main/java/app/dao"
        java.mkdir(parents=True)
        (java / "LegacyDAO.java").write_text(textwrap.dedent("""
            package app.dao;
            public class LegacyDAO {
                public Object find(Object p) { return helper.run(QUERY_FIND, p); }
            }
        """))
        xml = root / "src/main/resources/app/dao"
        xml.mkdir(parents=True)
        (xml / "legacy-sql.xml").write_text(
            '<mapper namespace="unrelatedNs">\n' + "  <!-- pad -->\n" * 50 + "</mapper>\n")
        idx = scan._Index(root)
        got = [q.name for q in idx.mybatis_xml_for(java / "LegacyDAO.java")]
        assert "legacy-sql.xml" in got, got


# ---- K2(a) / C3 / C5: 보고서 정직성 -----------------------------------------

def _report():
    _require_sut()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "kn_estimator.cli", str(SUT),
                        "--label", "template", "--model", "sonnet", "--out-dir", tmp],
                       check=True, capture_output=True, cwd=REPO)
        return (Path(tmp) / "kn-report.md").read_text()


def test_report_shows_a_prediction_interval_not_only_a_point_estimate():
    """run 분산이 ±30~46%인데 점추정만 보이면 거짓 정밀도다.

    "예측구간" 문자열만 단언하면 항진명제다 — 산출 실패 분기에도 그 단어가 들어 있어
    구간이 사라져도 통과한다. 실제 달러 범위와 그것이 점추정을 감싸는지를 본다.
    """
    txt = _report()
    m = re.search(r"예측구간: \$([\d,]+) ~ \$([\d,]+)", txt)
    assert m, f"달러 범위가 없다:\n{txt[:400]}"
    lo, hi = (int(g.replace(",", "")) for g in m.groups())
    point = int(float(re.search(r"예상 총비용: \*\*\$([\d.]+)\*\*", txt).group(1)))
    assert lo < point < hi, (lo, point, hi)
    assert hi > lo * 1.3, f"구간이 실측 run 분산(±30~46%)보다 좁다: {lo}~{hi}"


def test_plan_interval_is_computed_on_the_actual_partition():
    """구간은 선택된 파티션에서 재시뮬레이션해야 한다.

    167개를 한 청크로 보는 estimate_cell 비율을 이식하면, peak_context가 w_hard를 3배
    넘는 — 플랜이 스스로 거부할 — 구성의 α 민감도를 쓰게 된다.
    """
    _require_sut()
    cal, sls = _cal(), scan.build_slices(str(SUT), scan.inventory(str(SUT)))
    p = plan.build_plan(sls, cal, label="template", mdl="sonnet")
    lo, hi = cli._plan_interval(cal, "template", "sonnet", sls, p, plan.W_SOFT_DEFAULT)
    assert lo < p["total_cost_usd"] < hi, (lo, p["total_cost_usd"], hi)
    # 단일 mega-chunk 이식본보다 좁아야 한다 (그 쪽이 α 민감도를 과장한다)
    w_mean = sum(s["w_tokens"] for s in sls) / len(sls)
    est = model.estimate_cell(cal, "template", "sonnet", [s["w_tokens"] / w_mean for s in sls])
    transplanted_hi = p["total_cost_usd"] * est["pi_high"] / est["cost_usd"]
    assert hi < transplanted_hi, (hi, transplanted_hi)


def test_matrix_does_not_crash_when_some_cell_is_infeasible():
    """선택 셀은 되는데 다른 셀이 벽을 못 맞추는 경우.

    template/haiku(env≈90K)는 130K 벽에서 동작하지만 template/sonnet(env≈190K)·
    flat/sonnet(env≈118K+δ̂)은 못 맞춘다. K4 수정이 선택 셀만 다루면 매트릭스
    루프에서 KeyError로 크래시가 옮겨간다.
    """
    _require_sut()
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable, "-m", "kn_estimator.cli", str(SUT),
                            "--label", "template", "--model", "haiku",
                            "--w-hard", "130000", "--out-dir", tmp],
                           capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 0, f"크래시:\n{r.stderr[-600:]}"
        txt = (Path(tmp) / "kn-report.md").read_text()
        assert "infeasible_w_hard" in txt, "벽을 못 맞춘 셀이 표기되지 않았다"


def test_report_does_not_call_size_ranking_complexity():
    """w_i는 크기다 — 복잡도를 측정하지 않으므로 그렇게 라벨하면 안 된다."""
    txt = _report()
    assert "복잡도 상위" not in txt, "크기 순위를 '복잡도'라고 라벨하고 있다"


def test_report_discloses_that_complexity_is_not_modelled():
    txt = _report()
    assert "복잡도" in txt and "미반영" in txt, "복잡도 미반영 고지가 없다"


if __name__ == "__main__":
    passed = skipped = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except SkipTest as e:
            skipped += 1
            print(f"SKIP {name}: {e}")
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed} passed, {skipped} skipped")
