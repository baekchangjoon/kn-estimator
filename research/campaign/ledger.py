"""원장 기록 — kn-calibrate가 소비하는 run_total 행을 append.

calibrate.py가 요구하는 필드: run_id, variant(ARM_TO_CELL 키), role="run_total",
n, rep, gate, output_tokens, cost_usd, wall_s. 비용·토큰은 claude -p의 result.json
(total_cost_usd, usage)에서 취한다 — 원본 parse_transcript.py의 가격표 재계산 대신
CLI가 보고하는 실측값을 쓴다 (동일 출처: API usage).
"""
import json
import sys
from pathlib import Path


def main():
    ledger_path, run_id, variant, n, rep, gate, wall_s, result_json = sys.argv[1:9]
    res = json.loads(Path(result_json).read_text())
    u = res.get("usage") or {}
    row = {
        "run_id": run_id, "variant": variant, "role": "run_total",
        "n": int(n), "rep": int(rep), "gate": gate, "wall_s": int(wall_s),
        "cost_usd": res.get("total_cost_usd", 0.0),
        "output_tokens": u.get("output_tokens", 0),
        "input_tokens": u.get("input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "cache_write_tokens": u.get("cache_creation_input_tokens", 0),
        "num_turns": res.get("num_turns"),
        "model_resolved": (res.get("modelUsage") and sorted(res["modelUsage"])) or None,
        "session_id": res.get("session_id"),
    }
    with open(ledger_path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"ledger += {run_id} gate={gate} cost=${row['cost_usd']:.2f} out={row['output_tokens']}")


if __name__ == "__main__":
    main()
