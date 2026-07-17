#!/usr/bin/env bash
# 캠페인 드라이버 — 전 run 순차 실행. 이미 원장에 있는 run_id는 건너뛴다(재개 가능).
# 비용 가드: 누적 cost_usd가 상한을 넘으면 중단.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
COST_CAP="${COST_CAP:-200}"
PROJECTS="${PROJECTS:-petclinic auth-user community}"
ARMS="${ARMS:-flat_template_sonnet flat_sonnet flat_template_haiku}"
REPS="${REPS:-1 2 3}"
PY="$HERE/../../.venv/bin/python"

total_cost() {
  $PY - <<'PYEOF'
import json, glob, sys
from pathlib import Path
here = Path(sys.argv[0]).parent if False else None
tot = 0.0
for lp in glob.glob("/home/baek/temp/kn-wt/fix-inventory-recall-jpa-1hop/results/campaign/*/run_ledger.jsonl"):
    for l in Path(lp).read_text().splitlines():
        tot += json.loads(l).get("cost_usd", 0) or 0
print(f"{tot:.2f}")
PYEOF
}

for PROJECT in $PROJECTS; do
  NPOINTS=$($PY -c "import json;print(' '.join(str(x) for x in json.load(open('$HERE/targets.json'))['$PROJECT']['n_points']))")
  for ARM in $ARMS; do
    for N in $NPOINTS; do
      for REP in $REPS; do
        RUN_ID="${PROJECT}_${ARM}-n${N}-r${REP}"
        LEDGER="$HERE/../../results/campaign/$PROJECT/run_ledger.jsonl"
        if [ -f "$LEDGER" ] && grep -q "\"$RUN_ID\"" "$LEDGER"; then
          echo "[skip] $RUN_ID (already in ledger)"
          continue
        fi
        COST=$(total_cost)
        if [ "$(echo "$COST > $COST_CAP" | bc)" = "1" ]; then
          echo "COST CAP REACHED: \$$COST > \$$COST_CAP — 중단"
          exit 3
        fi
        echo "=== [$RUN_ID] 시작 (누적 \$$COST) ==="
        bash "$HERE/run_one.sh" "$PROJECT" "$ARM" "$N" "$REP"
      done
    done
  done
done
echo "campaign complete. total=\$$(total_cost)"
