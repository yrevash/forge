#!/bin/zsh
# Carry on the wide-data run after the first audited set exists (see wide_v3_pipeline.sh):
# more training parts, 100 rounds (5,200 parts) at a time, each recorded as further shards of
# train_wide_v3, until STOP_AT (default 05:30) or until the battery is below 30% while
# discharging. Then ALWAYS: manifest, leak check, grid table, proofs, audit, baselines.
#   nohup scripts/wide_v3_continue.sh >> data/freecad/sessions_wide/logs/pipeline.out 2>&1 &
cd "$(dirname "$0")/.." || exit 1
LOGS=data/freecad/sessions_wide/logs
TESTS=(wide_iid_v3 wide_numbers_v3 wide_order_v3 abnormal_starts_v3)
say() { echo "$(date '+%m-%d %H:%M') $*" | tee -a $LOGS/status.txt }
low() {     # is the battery low and not charging?
  pmset -g batt | grep -q "AC Power" && return 1
  local left=$(pmset -g batt | grep -o '[0-9]*%' | head -1 | tr -d '%')
  [ "${left:-100}" -lt 30 ]
}
late() { local now=$(date +%H%M); [ $now -ge ${STOP_AT:-0530} ] && [ $now -lt 1200 ] }
# The worker count is read before every stretch from logs/workers (default 10), so it can be
# changed while this runs:  echo 12 > data/freecad/sessions_wide/logs/workers
workers() { cat $LOGS/workers 2>/dev/null || echo 10 }
stage() { local name=$1; shift; "$@" >> $LOGS/$name.log 2>&1; say "$name: exit $? | $(tail -1 $LOGS/$name.log | cut -c1-220)" }
record() {  # in stretches of 20 minutes, so that a low battery is noticed soon
  while true; do
    low && { say "battery low: recording stopped (resumable)"; return 1; }
    uv run python -m forge.freecad.wide_sessions --workers $(workers) --max-minutes 20 --slices "$@" >> $LOGS/record.log 2>&1
    tail -3 $LOGS/record.log | grep -q "^shards left: 0" && break
    tail -3 $LOGS/record.log | grep -q "^shards left" || { say "recorder failed: see $LOGS/record.log"; return 1; }
  done
  say "recorded $*: $(uv run python -m forge.freecad.wide_sessions --manifest-only | tr -d '\n ' | cut -c1-420)"
}

record $TESTS train_wide_v3          # whatever of the first set is not recorded yet
grep -q "audit_interim" $LOGS/status.txt || \
  stage audit_interim uv run python -m forge.freecad.wide_audit --replay-sample 1500 --workers $(workers) --allow-excluded
have=$(cat data/system1/wide_parts/wide_train/wide_block.jsonl | wc -l); have=$((have / 13))
for cell in 300 400 500 600 700 800 900 1000 1100 1150; do
  [ $cell -le $have ] && continue
  if late || low; then say "stopping before $cell parts per cell (late or battery low)"; break; fi
  stage parts_train uv run python -m forge.freecad.wide_parts --set wide_train --per-cell $cell --workers $(workers)
  record train_wide_v3 || break
done
stage manifest uv run python -m forge.freecad.wide_sessions --manifest-only
stage leak_check uv run python -m forge.freecad.wide_parts --check
stage stats uv run python -m forge.freecad.wide_stats
stage audit uv run python -m forge.freecad.wide_audit --replay-sample 1800 --workers $(workers) --allow-excluded
stage baselines uv run python -m forge.freecad.baselines --dir data/freecad/sessions_wide --fit train_wide_v3 --eval $TESTS --workers $(workers)
stage prove_recipes uv run python -m forge.freecad.wide_prove --recipes 300 --accepted --sets wide_iid wide_numbers wide_order wide_starts wide_train --workers $(workers)
say "continue script finished"
