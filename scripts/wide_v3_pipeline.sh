#!/bin/zsh
# The whole wide-data run, in resumable stretches. Run it again to carry on.
#   nohup scripts/wide_v3_pipeline.sh > data/freecad/sessions_wide/logs/pipeline.out 2>&1 &
# It stops by itself, leaving everything resumable, when the battery is below 35% (and the
# laptop is not on mains). It uses 7 workers, 6 when the load average is above 20, and
# starts no new stretch of training parts after STOP_AT (default 03:45).
# Progress: data/freecad/sessions_wide/logs/status.txt (one line per finished stage).
cd "$(dirname "$0")/.." || exit 1
LOGS=data/freecad/sessions_wide/logs
mkdir -p $LOGS
TESTS=(wide_iid_v3 wide_numbers_v3 wide_order_v3 abnormal_starts_v3)

say() { echo "$(date '+%m-%d %H:%M') $*" | tee -a $LOGS/status.txt }
guard() {   # stop cleanly when the battery is low
  if ! pmset -g batt | grep -q "AC Power"; then
    local left=$(pmset -g batt | grep -o '[0-9]*%' | head -1 | tr -d '%')
    if [ "${left:-100}" -lt 35 ]; then say "battery at ${left}%: stopping; run the script again on mains"; exit 0; fi
  fi
}
workers() { [ "$(sysctl -n vm.loadavg | awk '{print int($2)}')" -gt 20 ] && echo 6 || echo 7 }
late() { local now=$(date +%H%M); [ $now -ge ${STOP_AT:-0345} ] && [ $now -lt 1200 ] }   # no new stretch after this
stage() {   # stage <name> <command...>: run, log, note the result
  local name=$1; shift
  guard
  "$@" >> $LOGS/$name.log 2>&1
  say "$name: exit $? | $(tail -1 $LOGS/$name.log | cut -c1-220)"
}
record() {  # record <slices...> until no shard is left, in stretches of 40 minutes
  while true; do
    guard
    uv run python -m forge.freecad.wide_sessions --workers $(workers) --max-minutes 40 --slices "$@" >> $LOGS/record.log 2>&1
    grep -q "^shards left: 0" <(tail -4 $LOGS/record.log) && break
    tail -3 $LOGS/record.log | grep -q "^shards left" || { say "recorder failed: see $LOGS/record.log"; exit 1; }
  done
  say "recorded $*: $(uv run python -m forge.freecad.wide_sessions --manifest-only | tr -d '\n ' | cut -c1-400)"
}

# 1. Test parts, and the first 200 rounds of training parts (one round = one part per cell).
stage parts_tests uv run python -m forge.freecad.wide_parts --set wide_iid wide_numbers wide_order wide_starts --workers $(workers)
stage parts_train uv run python -m forge.freecad.wide_parts --set wide_train --per-cell 200 --workers $(workers)
# 2. Tests first, then the first training stretch; audit; that is the interim state.
record $TESTS
record train_wide_v3
stage audit_interim uv run python -m forge.freecad.wide_audit --replay-sample 1500 --workers $(workers) --allow-excluded
# 3. More training parts and their sessions, 100 rounds (5,200 parts) at a time, until it is late.
for cell in 300 400 500 600 700 800 900 1000 1100 1150; do
  late && { say "it is late: no more training parts tonight (run again with STOP_AT=2359 to go on)"; break; }
  stage parts_train uv run python -m forge.freecad.wide_parts --set wide_train --per-cell $cell --workers $(workers)
  record train_wide_v3
done
# 4. The optional long training plans (14 to 16 items): only when there is time.
if ! late; then
  stage parts_train_long uv run python -m forge.freecad.wide_parts --set wide_train_long --per-cell 100 --workers $(workers)
  record train_wide_long_v3
fi
stage leak_check uv run python -m forge.freecad.wide_parts --check
# 5. The proofs, the audit over everything, the floor, the grid table.
stage stats uv run python -m forge.freecad.wide_stats
stage prove_recipes uv run python -m forge.freecad.wide_prove --recipes 250 --accepted --sets wide_iid wide_numbers wide_order wide_starts wide_train --workers $(workers)
stage prove_starts uv run python -m forge.freecad.wide_prove --starts 40 --sets wide_iid --workers $(workers)
stage prove_orders uv run python -m forge.freecad.wide_prove --orders 500 --sets wide_train composed --workers $(workers)
stage audit uv run python -m forge.freecad.wide_audit --replay-sample 1800 --workers $(workers) --allow-excluded
stage baselines uv run python -m forge.freecad.baselines --dir data/freecad/sessions_wide --fit train_wide_v3 --eval $TESTS --workers $(workers)
say "pipeline finished"
