"""`kn-estimate --groups`: 비용 최적 생성 묶음을 사람이 바로 실행할 형태로 출력.

"A 백엔드의 테스트 생성 비용 최적화된 생성 묶음을 보여줘"라는 요청에
"그룹1(q, w, e), 그룹2(z, x, y)로 돌리세요" 형태로 답하기 위한 CLI 출력 모드
(2026-07-26). 스킬/에이전트가 이 출력을 그대로 중계한다.
"""
import sys
import textwrap

from kn_estimator import cli


def _project(tmp_path):
    files = {
        "src/main/java/com/x/AlphaController.java": """\
            package com.x;

            @RestController
            public class AlphaController {
                @GetMapping("/a1")
                public ResponseEntity<String> a1() { return ResponseEntity.ok("1"); }

                @GetMapping("/a2")
                public ResponseEntity<String> a2() { return ResponseEntity.ok("2"); }
            }
        """,
        "src/main/java/com/x/BetaController.java": """\
            package com.x;

            @RestController
            public class BetaController {
                @GetMapping("/b1")
                public ResponseEntity<String> b1() { return ResponseEntity.ok("1"); }
            }
        """}
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return str(tmp_path)


def test_groups_flag_prints_runnable_grouping(tmp_path, monkeypatch, capsys):
    """다중 그룹 구성(--w-soft 176000 → 2그룹)에서 검증한다 — 단일 그룹 픽스처는
    '청크당 1줄'·'그룹 간 분할' 검증이 공허해져 뮤테이션이 생존한다 (리뷰 지적)."""
    import json
    from pathlib import Path
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--label", "template",
                         "--model", "sonnet", "--w-soft", "176000", "--groups"])
    cli.main()
    out = capsys.readouterr().out
    pj = json.loads((Path(root) / ".kn" / "kn-plan.json").read_text())
    assert pj["n_chunks"] >= 2, "픽스처가 다중 그룹을 만들지 못하면 이 테스트는 무의미하다"
    # 그룹 수 == 플랜 청크 수 (그 이상도 이하도 아님)
    group_lines = [l for l in out.splitlines() if l.strip().startswith("그룹")]
    assert len(group_lines) == pj["n_chunks"], out
    # 각 그룹 라인은 비용·peak를 담는다
    for l in group_lines:
        assert "— $" in l and "peak" in l, l
    # 모든 EP가 그룹 전체에 정확히 한 번 등장 (분할·중복·누락 방지)
    for ep in ("GET /a1", "GET /a2", "GET /b1"):
        assert sum(l.count(ep) for l in group_lines) == 1, (ep, out)
    # 실행 지시문과 독립 세션 조건(이게 1차 비용의 전제) 명시
    assert "돌리세요" in out
    assert "독립" in out


def test_groups_costs_are_consistent_with_total(tmp_path, monkeypatch, capsys):
    """그룹 비용의 합이 같은 출력의 총액과 정합해야 한다 — soft 페널티는 그룹
    라인에 반영하고, 병렬 할증은 총액에 명시한다 (리뷰 지적: 페널티 전 값을
    나열하면 사용자 합산이 총액과 어긋난다)."""
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--groups", "--parallel"])
    cli.main()
    out = capsys.readouterr().out
    assert "할증" in out   # 병렬 5% 할증이 총액에 포함됐음을 명시
    group_lines = [l for l in out.splitlines() if l.strip().startswith("그룹")]
    total = float(out.split("예상 총 $")[1].split(",")[0].split(".")[0]
                  + "." + out.split("예상 총 $")[1].split(".")[1][:2])
    group_sum = sum(float(l.split("— $")[1].split(",")[0]) for l in group_lines)
    # 총액 = 그룹 합 × 1.05 (병렬 할증) — 반올림 오차 허용
    assert abs(group_sum * 1.05 - total) < 0.05, (group_sum, total, out)
