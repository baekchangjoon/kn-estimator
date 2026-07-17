#!/usr/bin/env bash
# 한 run 실행: run_one.sh <project> <arm> <N> <rep>
#   arm ∈ flat_sonnet | flat_template_sonnet | flat_template_haiku (ARM_TO_CELL 키)
# 원본 reduce-token/harness/run_experiment.sh의 이식 — 전용 워크스페이스 클론에서 실행.
set -uo pipefail
PROJECT="$1"; ARM="$2"; N="$3"; REP="$4"
HERE="$(cd "$(dirname "$0")" && pwd)"
RT=/home/baek/temp/reduce-token
CFG="$RT/harness/claude-config"
WS_ROOT=/home/baek/temp/campaign-ws
RESULTS="$HERE/../../results/campaign/$PROJECT"
RUN_ID="${PROJECT}_${ARM}-n${N}-r${REP}"
OUT="$RESULTS/runs/$RUN_ID"
EPJ="$HERE/endpoints-$PROJECT.json"
PY="$HERE/../../.venv/bin/python"

SRC=$($PY -c "import json;print(json.load(open('$HERE/targets.json'))['$PROJECT']['src'])")
PIN=$($PY -c "import json;print(json.load(open('$HERE/targets.json'))['$PROJECT']['pin'])")
COMPILE=$($PY -c "import json;print(json.load(open('$HERE/targets.json'))['$PROJECT']['compile'])")

case "$ARM" in
  flat_sonnet)          MODEL="sonnet"; PROMPT_ARM="flat" ;;
  flat_template_sonnet) MODEL="sonnet"; PROMPT_ARM="flat_template" ;;
  flat_template_haiku)  MODEL="haiku";  PROMPT_ARM="flat_template" ;;
  *) echo "unknown arm $ARM"; exit 2 ;;
esac

mkdir -p "$OUT"
WS="$WS_ROOT/$PROJECT"
if [ ! -d "$WS/.git" ]; then
  mkdir -p "$WS_ROOT"
  git clone -q --local "$SRC" "$WS" || exit 1
fi

echo "[$RUN_ID] reset workspace"
git -C "$WS" checkout -qf "$PIN" && git -C "$WS" clean -qfdx || exit 1

# nimbus 스킬 + steering 배치 (원본 flat 경로와 동일)
$PY - <<PYEOF
import sys, shutil
from pathlib import Path
sys.path.insert(0, "$RT/port")
import build_port
build_port._copy_skills("$WS")
(Path("$WS")/"nimbus-prompts/steering").mkdir(parents=True, exist_ok=True)
for st in Path("$RT/nimbus/steering").glob("*.md"):
    shutil.copy(st, Path("$WS")/"nimbus-prompts/steering"/st.name)
PYEOF
case "$ARM" in flat_template*)
  mkdir -p "$WS/nimbus-skills/template-render"
  cp "$RT/port/template/main.py" "$RT/port/template/spec-schema.md" "$WS/nimbus-skills/template-render/"
  cp -r "$RT/port/template/resources" "$WS/nimbus-skills/template-render/resources"
;; esac

$PY "$HERE/render.py" "$PROMPT_ARM" "$RUN_ID" "$N" "$EPJ" > "$OUT/prompt.md" || exit 1

echo "[$RUN_ID] claude run (model=$MODEL)"
TS0=$(date -u +%s)
(
  cd "$WS" || exit 1
  env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT CLAUDE_CONFIG_DIR="$CFG" \
    timeout 3600 claude -p "$(cat "$OUT/prompt.md")" \
    --model "$MODEL" --output-format json --dangerously-skip-permissions \
    > "$OUT/result.json" 2> "$OUT/stderr.log"
)
RC=$?
TS1=$(date -u +%s)
WALL=$((TS1 - TS0))

JQ() { $PY -c "import json,sys;d=json.load(open('$OUT/result.json'));print(d$1)"; }
if [ $RC -ne 0 ] || ! JQ "['session_id']" >/dev/null 2>&1; then
  echo "[$RUN_ID] claude FAILED rc=$RC"; tail -3 "$OUT/stderr.log" 2>/dev/null
  echo "{\"run_id\":\"$RUN_ID\",\"variant\":\"$ARM\",\"role\":\"run_total\",\"n\":$N,\"rep\":$REP,\"gate\":\"error\",\"wall_s\":$WALL,\"cost_usd\":0,\"output_tokens\":0}" >> "$RESULTS/run_ledger.jsonl"
  exit 1
fi
SID=$(JQ "['session_id']")
TR=$(find "$CFG/projects" -name "${SID}.jsonl" 2>/dev/null | head -1)
if [ -z "$TR" ]; then echo "[$RUN_ID] transcript not found ($SID)"; exit 1; fi
cp "$TR" "$OUT/transcript.jsonl"

echo "[$RUN_ID] gate"
if $PY "$HERE/gate.py" "$WS" "$EPJ" "$N" "$COMPILE" > "$OUT/gate.log" 2>&1; then
  GATE="pass"
else
  GATE="fail"
fi
tail -3 "$OUT/gate.log"

mkdir -p "$RESULTS"
$PY "$HERE/ledger.py" "$RESULTS/run_ledger.jsonl" "$RUN_ID" "$ARM" "$N" "$REP" "$GATE" "$WALL" "$OUT/result.json"
echo "[$RUN_ID] done gate=$GATE wall=${WALL}s"
