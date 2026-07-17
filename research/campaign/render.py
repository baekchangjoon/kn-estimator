"""프롬프트 렌더 — 원본(reduce-token port/orchestrator)의 템플릿을 그대로 쓴다.

원본 render.py와의 차이: n1/n8 하드코딩 대신 f"n{N}" 키로 일반화, 엔드포인트
파일 경로를 인자로 받는다. 템플릿 파일은 읽기 전용 참조(수정 금지 규약).
"""
import json
import sys
from pathlib import Path

ORCH = Path("/home/baek/temp/reduce-token/port/orchestrator")


def render(arm, run_id, n, endpoints_path):
    eps = json.loads(Path(endpoints_path).read_text())
    sel = eps[f"n{int(n)}"]
    assert len(sel) == int(n), f"n{n} 항목 수 불일치: {len(sel)}"
    table = "\n".join(
        f"- {e['method']} {e['path']}  (controller: {e['file']}, handler: {e['handler']})"
        for e in sel)
    instr = (ORCH / "_instrumentation.md").read_text()
    body = (ORCH / f"{arm}.md").read_text()
    out = body.replace("{{ARM}}", arm).replace("{{RUN_ID}}", run_id) \
              .replace("{{N}}", str(n)).replace("{{ENDPOINT_TABLE}}", table) \
              .replace("{{INSTRUMENTATION}}", instr.replace("{{RUN_ID}}", run_id))
    if "{{STEERING}}" in out:
        out = out.replace("{{STEERING}}", (ORCH / "flat_steering.md").read_text())
    assert "{{" not in out, "unresolved placeholder"
    return out


if __name__ == "__main__":
    print(render(*sys.argv[1:5]))
