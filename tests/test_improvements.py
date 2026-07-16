"""B단계 개선 회귀 테스트 — 착수 전 red로 결함을 고정한다.

다루는 결함:
- K4: 모든 W_target frac이 벽을 넘으면 `best is None`으로 크래시.
- external_call: EXTERNAL_CALL_TYPES가 SPRING_INFRA의 부분집합이라 도달 불가 → 항상 False.
- K2(a): 예측구간 로직(estimate_cell)이 있는데 보고서가 점추정만 낸다.
- C3/C5: 보고서가 크기 순위를 "복잡도"라 라벨하고, 복잡도 미반영을 고지하지 않는다.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from kn_estimator import cli, model, plan, scan

REPO = Path(__file__).resolve().parents[1]
SUT = Path(os.environ.get("KN_SUT") or REPO / "legacy-sut")


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
    got = plan.build_plan(sls, _cal(), mode="flat", mdl="opus", w_hard=1000)
    assert isinstance(got, dict), type(got)
    assert got.get("status") == "infeasible_w_hard", got.get("status")
    assert "w_hard" in got


def test_build_plan_still_works_at_realistic_walls():
    """정상 경로는 그대로여야 한다 (위 수정이 회귀를 만들지 않았는지)."""
    _require_sut()
    sls = scan.build_slices(str(SUT), scan.inventory(str(SUT)))
    got = plan.build_plan(sls, _cal(), mode="template", mdl="sonnet")
    assert got["n_chunks"] == 60, got["n_chunks"]
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


# ---- K2(a) / C3 / C5: 보고서 정직성 -----------------------------------------

def _report():
    _require_sut()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "kn_estimator.cli", str(SUT),
                        "--mode", "template", "--model", "sonnet", "--out-dir", tmp],
                       check=True, capture_output=True, cwd=REPO)
        return (Path(tmp) / "kn-report.md").read_text()


def test_report_shows_a_prediction_interval_not_only_a_point_estimate():
    """run 분산이 ±30~46%인데 점추정만 보이면 거짓 정밀도다."""
    txt = _report()
    assert "예측구간" in txt, "보고서에 예측구간이 없다"


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
