# remote/ — record FreeCAD sessions on a borrowed Linux machine (Kaggle)

The recorders (`forge/freecad/sessions.py`, `forge/freecad/wide_sessions.py`,
`forge/freecad_multi/sessions.py`) drive real FreeCAD, which uses one CPU core per worker.
This folder lets the same recorders run on Kaggle's free CPU kernels, so the laptop is not
the only place data can be made. Nothing outside this folder was changed for it.

No model is trained or defined here, and no training example is written by hand: the
shards are made by the recorders' own `write_shard`, from the parts and plans the Mac chose.

```
remote/
  linux.py          point the client at a Linux FreeCAD; the CadQuery helper without the macOS sandbox
  bundle.py         [Mac]    pack our code and the chosen shards' inputs into bundle.bin   [command]
  kernel_dir.py     [Mac]    write the folder `kaggle kernels push` wants                  [command]
  kaggle_kernel.py  [Kaggle] install FreeCAD 1.1.4, then record; writes report.json
  record.py         [either] record the shards of a task file, resumable by shard          [command]
  compare.py        [Mac]    far shards against the Mac's shards of the same name          [command]
  check_labels.py   [Mac]    the audits' teacher / oracle / splits / endings checks on far shards  [command]
  replay.py         [Mac]    the audits' replay in this machine's FreeCAD, every far session  [command]
```

Tests: `tests/test_remote.py` (no FreeCAD, no network).

## How it fits together

```
Mac                                             Kaggle kernel (private, CPU only, internet on)
bundle.py   -> bundle.bin  --dataset-->         kaggle_kernel.py
  forge/** byte for byte                          download the official FreeCAD 1.1.4 AppImage,
  tasks_<recorder>.json: per shard,               unpack it under /tmp (no FUSE needed)
  exactly the parts the Mac would record          record.py -> the recorder's own write_shard
                                                  /kaggle/working/out/<recorder>/<slice>/shardNNN.*
compare.py / the audits  <--kernels output--      /kaggle/working/report.json
```

Why the Mac plans and Kaggle only plays: the recorders plan their shards from the whole of
`data/` (hundreds of MB). A task file holds only the parts of the chosen shards, and the
shard numbers are the recorders' own, so shard N made on Kaggle is shard N made here.

Why a shard made there can be trusted to be "ours": `record.py` refuses to record unless
the code on that machine hashes to the hash the tasks were planned with (the recorders'
own `code_hash`, the one stored in every shard).

## What is macOS-specific in the runtime, and what Linux does instead

| Mac | Linux | Changed? |
| --- | --- | --- |
| `locate.py` looks in `/Applications/FreeCAD.app` | `FORGE_FREECAD_PYTHON` / `FORGE_FREECAD_LIB`, which `locate.py` already reads; `linux.use_freecad` sets them | no code change |
| `client.py` wraps the worker in `sandbox-exec` | `client.py` already skips the wrapper when `sandbox-exec` is not installed, so the worker starts bare | no code change |
| `forge/sandbox.py` (CadQuery helper) refuses to start off macOS | `linux.LinuxSandbox`: the same helper process, time limit and kill, without the OS sandbox | new subclass, handed to `KernelJudge(sandbox)` |
| `inside/worker.py` memory report | already handles Linux (`ru_maxrss` in kilobytes) | no |

**The trade-off.** The macOS sandbox takes the network and all writes outside a temp
folder away from the process. It is there for code a model wrote. The recorders run only
our own deterministic code, and the FreeCAD worker runs nothing it is sent (a fixed
catalogue of commands with numbers). On a throw-away Kaggle container there is nothing
to protect either. So recording bare is acceptable. **It is not acceptable for
model output**: `forge/sandbox.py` still refuses on Linux, and `LinuxSandbox` must never be
given a program a model wrote. A real Linux sandbox (bubblewrap or nsjail) is still owed
before any model output is run on Linux.

A cleaner hook, at the cost of one line in `client.py`: `sandboxed=False` is already a
constructor argument, but the recorders build their clients themselves, so an environment
switch (`FORGE_FREECAD_SANDBOX=off`) read next to `shutil.which("sandbox-exec")` would make
the choice explicit instead of resting on the program being absent. Not needed today.

## Commands

```bash
# 1. Mac: choose shards (slice:first-last[:parts cut for a probe]) and pack
uv run python -m forge.remote.bundle --out /tmp/fb --wide train_wide_v3:42-86
printf '{"title": "forge-freecad-bundle", "id": "yashtiwari9182/forge-freecad-bundle", "licenses": [{"name": "other"}]}' > /tmp/fb/dataset-metadata.json
uvx kaggle datasets version -p /tmp/fb -m "wide 42-86"      # first time: datasets create -p /tmp/fb

# 2. Mac: one kernel folder per kernel; `only` are positions in the task file
uv run python -m forge.remote.kernel_dir --out /tmp/k1 --slug forge-fc-wide-1 \
    --dataset yashtiwari9182/forge-freecad-bundle \
    --config '{"burn": 0, "prove": 0, "jobs": [{"tasks": "tasks_wide.json", "out": "wide", "workers": 4, "only": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14], "max_minutes": 660}]}'
uvx kaggle kernels push -p /tmp/k1

# 3. Mac: wait, then collect (outputs exist only after the kernel has finished)
uvx kaggle kernels status yashtiwari9182/forge-fc-wide-1
uvx kaggle kernels output yashtiwari9182/forge-fc-wide-1 -p /tmp/k1_out

# 4. Mac: check (see "Equivalence" below), then move the shards into data/
```

**Checkpointing.** A recorder writes a shard's `.stats.json` last, so a shard is either
whole or absent. `record.py` skips shards whose stats file exists and, with
`max_minutes`, starts no new shard after that time: the kernel then ends normally and
Kaggle keeps `/kaggle/working`. A kernel that is killed at the 12-hour limit may keep
nothing, so `max_minutes` must leave room for the slowest shard. A second kernel never
sees the first one's files; to continue, bundle again with the shards that are still
missing (the collected folder says which).

## Equivalence: what "the same" means across machines

Three checks, strongest last:

1. `check_labels.py`: the audits' plain-Python checks (no FreeCAD).
2. `compare.py`: when the Mac has recorded the same shard, every record of every session
   against the Mac's. It sorts each session into `same`, `audit` (differs only where the
   audit's `same_lean` allows), `noise` (numbers below 1e-6 where `same_lean` is exact) and
   `content`.
3. `replay.py`: the audits' own replay in this machine's FreeCAD.

## Measured (probe of 8 Oct 2026)

- A Kaggle CPU kernel: 4 logical CPUs on 2 physical cores, 32 GB. Four kernels ran at once.
- The official 1.1.4 AppImage installs in under a minute and gives the Mac's versions
  exactly (FreeCAD 1.1.4, OpenCascade 7.8.1, Python 3.11.14). It is pinned by SHA-256 and
  verified before it is run.
- Proof: 300 of 300 parts the stored solid.
- Against the Mac's shards of the same parts and seeds: labels, plans, ids and targets the
  same in all 1,955 sessions; 92.5% to 94.6% of sessions identical in every record; the
  rest differ in last digits (x86_64 against arm64), in the wrong solid a mistaken
  `polar_pattern` leaves, and in which failing features hit the 30 s limit. As the audit
  is written, a Mac replay would reject 5.4% (single), 3.4% (wide), 1.1% (structures).
- Kaggle against Kaggle: structures 350 of 350 identical; single 1,393 of 1,400 (7 in a
  volume's last digits); wide 204 of 205 (one timeout).
- Speed per kernel: single 13,400 sessions/hour, structures 3,970, wide 1,250 to 1,640.
  Four kernels are about one laptop.

**Status: probe only.** Nothing recorded on Kaggle is training data yet
(the Mac replay and the 12-hour limit are not verified yet).
