# TRAAVIIS — Split Evaluation Closure (`trvs eval`)

**Date** 2026-07-24 · **Status** shipped, 15 new laws green, 314 tests green
**Scope** RFC_TRAAVIIS_ARTIFACTS.md §7 — the last unbuilt item in the build
order that is not explicitly deferred. `serve` (D5) and `bundle-…` remain open.

---

## 1. What shipped

`init` + `pack` closed authoring. `eval-one` closed one task. The remaining gap
was the one §7 names last:

> `trvs eval` — run an agent over a split, emit an `episode-…` per task, score.

```sh
trvs pack lab lab-pkg
trvs eval lab-pkg --split test --output episodes/ --agent python3 agent.py
trvs verify-episode episodes/episode-cfef6da4…      # replays with no agent
```

Verified end to end on a hand-authored three-task environment:

```
pack →  env-aa7df5dd…   task-3b4b2599…  task-082e11c1…  task-0cd4992e…
        closure ✓  reopened ✓

eval --split test
  [1/2] ✓ task-082e11c181f3be21aa82e138  ok  reward 1
  [2/2] ✓ task-0cd4992e274e07fc7da0bddd  ok  reward 1
  ok            2/2
  mean reward   1
  evaluation    episodes/evaluation.json

verify-episode (each, no agent process) → ✓ closed
```

## 2. The load-bearing decision: `eval` mints no identity

§1's ladder ends at `env-…` / `bundle-…`. There is **no rung for "a run over a
split"**, so this command does not create one. What it returns is an *index*:

```json
{ "evaluation_version": "traaviis.evaluation.v1",
  "env_id": "env-…", "split": "test",
  "episodes": [ {"task_id": "task-…", "episode_id": "episode-…",
                 "status": "ok", "reward": 1.0, "bundle": "episode-…"} ],
  "totals": {"tasks": 2, "ok": 2, "reward_mean": 1.0, …} }
```

Every id in it is derived somewhere else. The index itself is not
content-addressed and carries no id — a law (E5) scans the emitted JSON and
fails on any `<prefix>-<hex>` family outside `{env, task, episode, snap, sem}`,
and on any `evaluation_id`/`run_id`/`eval_id` key.

This is deliberate restraint, not an oversight. A defensible `eval-…` would have
to close over *the agent*, and there is no agent identity rung — so any id minted
today would be under-determined, and a later real one would silently disagree
with it. See §6.1.

## 3. Nothing runs until everything checkable has been checked

The order mirrors `pack`'s, for the same reason:

```
1. open_package     reopen through §5a — env-/task-/rew-/snap- ALL re-derived
                    from the written bytes
2. resolve split    the split exists, is non-empty, every member is in-closure
3. admit subject    bind the on-disk tree to snapshot.json (once, shared)
--- only now is a process launched ---
4. per task         evaluate → optionally persist episode-<id>/
5. aggregate        index written atomically (temp sibling + one rename)
```

Two laws prove the boundary is real rather than decorative: E2 tampers a packed
task and E2b drifts one subject byte, then passes an agent that writes a marker
file. Both refuse with a typed code (`REOPEN_TASK_ID` / `SUBJECT_*`) **and the
marker never appears** — a broken package costs zero episodes, not N wasted ones.

## 4. A split is a set — so its order is not observable

§3 says splits are "**named sets** of `task-…` ids". Taken literally that has a
consequence the previous `pack` did not honour: it stored split membership in
source order, so listing the same two tasks the other way round produced a
different `env-…`. Two identical environments, two identities.

`pack` now canonicalizes a split as a **sorted set**, and a repeated member is
`SPLIT_DUPLICATE` rather than being silently collapsed (pack never repairs a
scaffold). `eval` then derives its run order as the sorted task ids, so the
order tasks execute in is a function of the environment, never of how someone
typed the manifest. E1 packs two environments differing only in split order and
asserts byte-identical `env-…`.

## 5. Laws (15 new, all green)

`test/test_eval_split.py`:

- **E1** split order does not move `env-…`; the packed manifest is sorted ·
  duplicate member refused, nothing written.
- **E2** tampered package and drifted subject both refuse **before any agent
  process starts** (proved with a marker-writing agent).
- **E3** unknown / empty / foreign-member splits are typed refusals, and the
  unknown-split error names the splits that do exist.
- **E4** run order is the sorted task ids regardless of manifest order.
- **E5** the index invents no identity (id-family scan + forbidden keys).
- **E6/E10** one episode per task, distinct ids for distinct tasks, totals are
  real arithmetic over the episodes.
- **E8** persisted episodes are genuine closed bundles — each re-verified with
  `trvs verify-episode`, no agent — and the index round-trips.
- **E9** a bad task is recorded and the split continues (2 of 3 still score 1).
- **E11** a `trvm.world.v1` package is refused *by name*
  (`SUBSTRATE_NOT_EVALUABLE`), not half-run.
- **E13** two runs of one split agree exactly (`a == b`).
- CLI: exit 0 all-ok · exit 1 ran-and-disagreed (`ok 2/3`) · exit 2 unknown
  split / missing package · `--json` emits the index.

Regression: **314 tests green, 0 failures, 0 skips** — admission 16, cli_evalone
18, cli 12, episode_evidence 56, evalone 14, **eval_split 15**, execfacts 8,
forge_adapter 6, identity 25, pack 18, patchapply 10, paths 10, real_residency 9,
reward 17, runner 10, scaffold 16, snapshot 10, substrate_verifiers 24,
vcontext 5, verifiers 15.

## 6. Autonomous decisions — flagged for GPT-5.6

1. **`eval` mints no artifact id.** The index is `traaviis.evaluation.v1` and is
   not content-addressed. If you want a run over a split to *be* an artifact, it
   needs an agent identity rung first — otherwise the id closes over less than
   the thing it names. I did not invent either. **Confirm, or rule the agent
   rung.**
2. **Splits are canonicalized as sorted sets, and this changes `env-…`** for any
   environment whose split members were not already in sorted order. It is the
   literal reading of §3 ("named sets"), and it makes reordering non-identity —
   but it *is* a behaviour change to a shipped command. Confirm.
3. **`SPLIT_DUPLICATE` refuses rather than collapses** a repeated member,
   matching the `RemoveObject`/no-auto-repair discipline.
4. **Verifier wiring moved out of the CLI** into `traaviis/wiring.py`. It was
   CLI-private, so a library caller got *zero* verifiers and every episode came
   back invalid-config. A task's verifier set must be a function of the task, not
   of the caller, or two runs of one task are not comparable. This was a real
   defect found by the E6 law, not a refactor for taste.
5. **A low reward is not a disagreement.** Exit 1 fires only on `invalid` /
   `error` episodes; an agent that legitimately scores 0.25 exits 0. `eval`
   scores, it does not judge.
6. **One subject per environment**, admitted once and shared by every task in
   the split. That is the shape `pack` emits today. Per-task subjects would need
   a different manifest, not a different runner.
7. **`trvm.world.v1` is refused by name.** It packs and reopens fine, but the
   Episode Kernel over a world subject (D5) is unbuilt, so it is a typed refusal
   rather than a partial run.

## 7. Not built

`trvs serve --ors/--mcp` (needs the D5 process model), `bundle-…` (the outer
content-addressed package from D3), and the REPL. With `eval` shipped, **§7's
build order is exhausted** — everything remaining was explicitly deferred by
ruling, so this is a natural halt point.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
