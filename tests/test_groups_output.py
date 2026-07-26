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
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--mode", "template",
                         "--model", "sonnet", "--groups"])
    cli.main()
    out = capsys.readouterr().out
    # 그룹 라인: "그룹N(EP, EP, ...) — $비용" 형태, 모든 EP가 정확히 한 번 등장
    assert "그룹1(" in out
    for ep in ("GET /a1", "GET /a2", "GET /b1"):
        assert out.count(ep) == 1, (ep, out)
    # 실행 지시문과 독립 세션 조건(이게 1차 비용의 전제) 명시
    assert "돌리세요" in out
    assert "독립" in out


def test_groups_flag_group_count_matches_plan(tmp_path, monkeypatch, capsys):
    import json
    from pathlib import Path
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["kn-estimate", root, "--groups"])
    cli.main()
    out = capsys.readouterr().out
    pj = json.loads((Path(root) / ".kn" / "kn-plan.json").read_text())
    for i in range(1, pj["n_chunks"] + 1):
        assert f"그룹{i}(" in out
    assert f"그룹{pj['n_chunks'] + 1}(" not in out
