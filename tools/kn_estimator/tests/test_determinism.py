"""결정성 회귀 테스트 (0단계).

시딩 시점 결함: `_injected_types()`가 set을 반환하고 `visit()`이 깊이별 감쇠를 적용해,
공유 의존 타입의 가중치가 '어느 부모가 먼저 방문하느냐'로 갈린다. set 순회 순서는 Python
해시 랜덤화에 좌우되므로 실행마다 w_tokens가 달라진다 (실측 sum_w 변동 폭 ~0.4%).

수리: 순회를 BFS + 정렬로 바꿔 감쇠를 '최단 깊이'의 함수로 만든다 — 결정적이며,
알파벳 우연이 아니라 그래프 구조에만 의존한다.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PKG = Path(__file__).resolve().parents[1]
SUT = Path(os.environ.get("KN_SUT") or (REPO / "smartplant"))


class SkipTest(Exception):
    """건너뜀을 통과와 구분하기 위한 신호 — 조용한 green 금지."""


def _skip(reason):
    try:
        import pytest
    except ImportError:
        raise SkipTest(reason)
    pytest.skip(reason)

_SUM_W_SNIPPET = textwrap.dedent("""
    import sys
    sys.path.insert(0, sys.argv[1])
    import scan
    root = sys.argv[2]
    eps = scan.inventory(root)
    sls = scan.build_slices(root, eps)
    print(sum(s["w_tokens"] for s in sls))
""")


def _sum_w_under_seed(seed):
    """별도 프로세스에서 PYTHONHASHSEED를 고정해 sum_w를 구한다."""
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    out = subprocess.run([sys.executable, "-c", _SUM_W_SNIPPET, str(PKG), str(SUT)],
                         capture_output=True, text=True, env=env, check=True)
    return int(out.stdout.strip())


def test_sum_w_is_identical_across_hash_seeds():
    """해시 시드가 달라도 w 총합은 같아야 한다 (수리 전에는 실패한다)."""
    if not SUT.exists():
        _skip(f"SUT 없음 ({SUT}) — KN_SUT 환경변수로 지정 가능")
    seeds = [0, 1, 2, 3, 12345]
    sums = [_sum_w_under_seed(s) for s in seeds]
    assert len(set(sums)) == 1, f"해시 시드별 sum_w 불일치: {dict(zip(seeds, sums))}"


def test_injected_types_returns_deterministic_order():
    """_injected_types는 순서가 고정된 시퀀스를 돌려줘야 한다 (set 금지)."""
    sys.path.insert(0, str(PKG))
    import scan
    src = """
        public class Foo {
            @Autowired private ZebraService zebra;
            private final AlphaDAO alpha;
            @Autowired private MidRepository mid;
        }
    """
    got = scan._injected_types(src)
    # 내용까지 단언한다. `list(got) == sorted(got)`만으로는 구현이 문자 그대로
    # sorted()를 반환하므로 항진명제이고, 정규식이 깨져 []를 내도 통과한다.
    assert got == ["AlphaDAO", "MidRepository", "ZebraService"], got


def test_shared_dependency_uses_shortest_depth_regardless_of_traversal_order():
    """공유 타입은 최단 깊이의 감쇠를 받아야 한다 (BFS 성질).

    구조: Controller → AService → SharedDAO,  Controller → SharedDAO (직접)
    SharedDAO는 최단 깊이 1이므로, AService 경유(깊이 2)로 먼저 닿더라도 감쇠 1.0이어야 한다.
    알파벳순으로 AService < SharedDAO라, DFS에 정렬만 얹은 수리는 이 테스트를 통과하지 못한다.
    """
    sys.path.insert(0, str(PKG))
    import scan

    with tempfile.TemporaryDirectory() as tmp:   # 예외 경로에서도 정리된다
        _assert_shortest_depth_decay(scan, Path(tmp))


def _assert_shortest_depth_decay(scan, root):
    java = root / "src/main/java/app"
    java.mkdir(parents=True)
    (java / "DemoController.java").write_text(textwrap.dedent("""
        package app;
        @RestController
        @RequestMapping("/api")
        public class DemoController {
            @Autowired private AService aService;
            @Autowired private SharedDAO sharedDAO;
            @GetMapping("/x")
            public String handle() { return "x"; }
        }
    """))
    (java / "AService.java").write_text(textwrap.dedent("""
        package app;
        public class AService {
            @Autowired private SharedDAO sharedDAO;
        }
    """))
    (java / "SharedDAO.java").write_text("package app;\npublic class SharedDAO {\n" + "// pad\n" * 200 + "}\n")

    eps = scan.inventory(str(root))
    assert len(eps) == 1, eps
    sl = scan.build_slices(str(root), eps)[0]
    shared_tokens = scan.tokens_of(java / "SharedDAO.java")
    a_tokens = scan.tokens_of(java / "AService.java")
    # SharedDAO는 깊이 1 → 감쇠 1.0으로 전액 가산되어야 한다 (0.5 아님)
    expected_min = sl["handler_tokens"] + shared_tokens + a_tokens
    assert sl["w_tokens"] == expected_min, (
        f"w={sl['w_tokens']} != {expected_min} — SharedDAO가 최단깊이(1) 감쇠를 못 받음")


if __name__ == "__main__":
    passed, skipped = 0, 0
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
    if skipped:
        # skip을 green으로 주장하지 않는다 — 결정성 게이트가 조용히 통과되면 안 된다.
        sys.exit(1)
