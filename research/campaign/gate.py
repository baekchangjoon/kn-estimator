"""품질 게이트 — 원본 quality_gate.sh의 프로젝트 무관 부분을 이식.

① 커버리지: .nimbus/artifacts/manifest.json에 요청 엔드포인트 전부 status=ok + 파일 실존
② 컴파일: 대상 프로젝트의 test-compile (targets.json의 compile 커맨드)

원본의 ③ SQL/스텁 파싱은 MyBatis·LegacySut 전용이라 제외한다 (JPA 대상에는 대응물이
없고, 컴파일 게이트가 산출물 형식 오류 대부분을 잡는다). 테스트 실행은 원본도 하지
않는다 — 인프라(DB/Kafka) 불필요.
"""
import json
import subprocess
import sys
from pathlib import Path


def coverage(ws, endpoints_path, n):
    sel = json.loads(Path(endpoints_path).read_text())[f"n{int(n)}"]
    mf = Path(ws) / ".nimbus/artifacts/manifest.json"
    if not mf.exists():
        return False, "coverage FAIL: manifest.json missing"
    try:
        manifest = json.loads(mf.read_text())
    except Exception as e:
        return False, f"coverage FAIL: manifest unparseable: {e}"
    missing, bad = [], []
    for e in sel:
        key = f"{e['method']} {e['path']}"
        ent = manifest.get(key)
        if ent is None or ent.get("status") != "ok":
            missing.append(key)
            continue
        for f in ent.get("files", []):
            if not (Path(ws) / f).exists() and not Path(f).exists():
                bad.append(f"{key} -> {f}")
    if missing or bad:
        return False, f"coverage FAIL missing={missing} nonexistent={bad}"
    return True, "coverage OK"


def compile_gate(ws, compile_cmd):
    r = subprocess.run(compile_cmd, shell=True, cwd=ws,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        # 4000자: 컴파일 오류 목록의 선두(근본 원인)가 잘리지 않게 — 800자에서는
        # "package io.restassured does not exist"가 잘리고 후속 오류만 남았다.
        tail = (r.stdout + r.stderr)[-4000:]
        return False, f"compile FAIL rc={r.returncode}: {tail}"
    return True, "compile OK"


def main():
    ws, endpoints_path, n, compile_cmd = sys.argv[1:5]
    ok1, msg1 = coverage(ws, endpoints_path, n)
    print(msg1)
    ok2, msg2 = compile_gate(ws, compile_cmd)
    print(msg2)
    print("GATE:", "pass" if (ok1 and ok2) else "fail")
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
