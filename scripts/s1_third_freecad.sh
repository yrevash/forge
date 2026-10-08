#!/bin/zsh
# The FreeCAD work of the third Forge-S1 model. It is heavy, so it only runs with GO=1.
#
#   GO=1 WORKERS=6 THIRD=checkpoints/s1/<third model>.pt scripts/s1_third_freecad.sh <stage>
#
# Stages, each one job at a time, each writing its own runs/<date>-... folder and a log in
# runs/s1_third_logs/fc_<name>.log. Times are ESTIMATES from the interrupted run of 8 Oct
# (model 1, 926 episodes in 20 minutes on 6 workers; failing long episodes are the slow ones)
# and scale roughly with 6 / WORKERS:
#
#   baselines   models 1 and 2, "command choice with teacher-supplied arguments", seeds 0 1 2,
#               8 test slices x 2 conditions x 100 parts = 1,600 episodes a run, 6 runs.
#               About 30 to 40 min a run on 6 workers (3 to 4 h in all); 45 to 60 min on 4.
#   third       the third model: teacher-free (HEADLINE) and teacher arguments, seeds 0 1 2,
#               6 runs. About 20 to 30 min a run on 6 workers (2 to 3 h); 30 to 45 min on 4.
#   dagger N    DAgger round N with $THIRD: about 3,150 rollouts of TRAINING parts.
#               About 40 to 60 min on 6 workers, 60 to 90 on 4. Then (no FreeCAD):
#               uv run python -m forge.s1.third.prepare --slices dagger_rN   (2 workers, minutes)
#   probes      the two hand-written probe plans (an anecdote): 1 FreeCAD process, 2 minutes.
#   harness     the scripted teacher through the same driver; must build every part. 3 minutes.
set -e
cd "$(dirname "$0")/.."
[ "$GO" = "1" ] || { echo "held: run with GO=1"; exit 1; }
W=${WORKERS:-6}
L=runs/s1_third_logs
D="uv run python -m forge.s1.third.drive --workers $W"
run() { echo "$(date +%H:%M) START $1"; nice -n 5 env OMP_NUM_THREADS=1 ${=2} > $L/fc_$1.log 2>&1; echo "$(date +%H:%M) END $1 $(grep -h 'run folder' $L/fc_$1.log | tail -1)"; }
case "$1" in
  harness) run harness "$D --arguments model --parts 8" ;;
  baselines)
    for seed in 0 1 2; do
      run first_s$seed "$D --checkpoint checkpoints/s1/s1-first-best.pt --arguments teacher --seed $seed"
      run second_s$seed "$D --checkpoint checkpoints/s1/s1-second-noids-best.pt --arguments teacher --seed $seed"
    done ;;
  third)
    for seed in 0 1 2; do
      run third_free_s$seed "$D --checkpoint $THIRD --arguments model --seed $seed"
      run third_teacher_s$seed "$D --checkpoint $THIRD --arguments teacher --seed $seed"
    done ;;
  dagger) run dagger_r$2 "uv run python -m forge.s1.third.dagger --checkpoint $THIRD --round $2 --workers $W" ;;
  probes) run probes "uv run python -m forge.s1.third.probe --checkpoint $THIRD --arguments model data/s1/demo_stool/plan.json data/s1/demo_plate_flange/plan.json" ;;
  *) echo "stage: harness | baselines | third | dagger N | probes"; exit 1 ;;
esac
