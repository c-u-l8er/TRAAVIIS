# TRAAVIIS — Environment Authoring Closure (`trvs init` + `trvs pack`)

**Date** 2026-07-24 · **Status** shipped, 34 new laws green, full suite green
**Scope** RFC_TRAAVIIS_ARTIFACTS.md §5, §5a, §6 — the first two items of the
deferred surface that §7's build order unblocks once `eval-one` exists.

---

## 1. What shipped

`eval-one` closed the beachhead: one agent, one frozen subject, one
content-addressed receipt. But every bundle it could run had to be *hand-built*
(`examples/eval-one/residency-forge/build_bundle.py`). Authoring was the gap.

Two commands close it, and the split between them is the whole design:

```sh
trvs init --list                                  # templates + their substrates
trvs init --template evidence-residency my-env    # seeds a subject + skeletons
trvs pack my-env my-env-pkg                       # closes it into a package
trvs eval-one my-env-pkg --agent … --platform linux-x86_64
```

Verified end to end, from nothing to a scored episode:

```
init  →  8 files, identity: unresolved (init derives none)
pack  →  env-a38ec4c0…  snapshot snap-c66198ab…  task task-3b4b2599…
         closure  ✓ verified before write
         reopened ✓ re-derived from the written bytes
eval-one → status ✓ ok · validity valid · reward 1
           citations pass · patch pass · tests pass · identity pass ·
           finding_completeness pass
```

The scaffolded residency subject recomputes to **exactly** the frozen example's
`snap-c66198abf3ef6153d4bd6033fa40a0bd0028df3a7699ab645dc947710e51cba4` and
`rew-25c4ce1276a7b70a473548354ae5d14f1c852ba087ffd09bc9b17b550c5c05a5`, and the
`golden-spinner` template lowers to the frozen demo world
`sem-8ae91fe9cbc5fd086ce4356d…fe4a`. The seeds are the real artifacts, not
lookalikes.

## 2. The load-bearing decision: `init` derives nothing

§6 says `init` "invents no identity." Taken literally that forbids the obvious
implementation — scaffolding a `task.json` with a `reward_id` in it — because a
scaffold cannot know the hash of bytes the author has not written yet, and a
placeholder hash is a lie that later verifies.

So the scaffold emits **references**, and packing is the substitution:

| scaffold (`init`)              | package (`pack`)                          |
| ------------------------------ | ----------------------------------------- |
| `task.reward_spec: "reward.json"` | `task.reward_id: "rew-…"`               |
| `subject.snapshot_def: "snapshot_def.json"` | `subject.snapshot_id: "snap-…"` |
| `identity_policy…` *(no `before_id`)* | `before_id: "sem-…"` (lowered now)  |
| *(no `env_id`)*                | `env_id: "env-…"`                         |

That is why `init` needs no engine and `pack` does. It also makes L1 mechanically
checkable: `scaffold.identity_violations()` scans every emitted document for an
identity *key* at any depth **and** every emitted byte for a `<prefix>-<hex>`
literal, so a future template cannot quietly bake one in — including in prose.

## 3. `pack` follows §6's order literally

```
1. validate_subject             well-formed for the profile
2. recompute_subject_identity   snap-… / sem-… FROM THE BYTES
3. bind + close                 rew-… → task-… → env-…
4. verify_closure               every reference resolves
--- nothing has been written yet ---
5. write                        temp tree → one os.replace
6. reopen_package               re-derive every id from what landed on disk
```

Step 6 is not ceremony. `pack` deliberately does not trust its own in-memory
computation: it reads the package back, re-derives `env-`/`task-`/`rew-`/`snap-`
from the written bytes, **rebuilds the snapshot from the written subject tree**,
and re-lowers the written world. If any of that disagrees, the package is
removed and the command fails. Tampering with a packed task, or with one byte of
a packed subject, is caught (`REOPEN_TASK_ID`, `REOPEN_SUBJECT_BYTES`).

Everything is fail-closed with a typed code: `SOURCE_PREBOUND`, `SUBJECT_DRIFT`,
`SUBJECT_KIND`, `CLOSURE_VERIFIER`, `CLOSURE_SUBJECT_BINDING`, `SPLIT_UNRESOLVED`,
`DEST_NOT_EMPTY`, `ENGINE_UNAVAILABLE`, `REOPEN_*`. A refused pack writes nothing.

## 4. The §5a admission interface is real

`traaviis/substrates.py` implements `validate_subject · verify_closure ·
recompute_subject_identity · reopen_package` as a base class plus two profiles:

- `trvm.world.v1` — subject is WRL source; identity is `engine.lower_source()` →
  `sem-…`; reopen re-lowers the written world.
- `residency.repository.v1` — subject is a repository; identity is
  `snapshot.build_snapshot()` → `snap-…`; reopen rebuilds it from the written
  tree; closure additionally checks the verifier plan is *answerable by the
  reward it is bound to* and that the test plan declares a runner profile.

The packer branches on profile in exactly two places (which subject bytes to
copy, and the eval-bundle emission). A third substrate is a new profile object,
not a new branch — which was the point of §5a.

## 5. Laws (34 new, all green)

`test/test_scaffold.py` — 16 laws:
L1 invents no identity (incl. a planted-id detector test, so the check itself is
proven to fire) · L2 deterministic (pure + on-disk) · L3 substrate-distinct ·
L4 declared frozen versions · L5 fail-closed + atomic (non-empty dest refused,
mid-write failure leaves no partial tree and no temp dir) · L6 admissible input
(snapshot definition matches the seeded tree exactly; both seeded worlds lower) ·
L7 portable (no absolute/host paths) · plus the CLI contract.

`test/test_pack.py` — 18 laws:
P1 every id recomputed, pre-bound ids refused · P2 content moves identity
(subject bytes → `snap-`+`env-`; task instructions → `task-`+`env-`) ·
**P3 presentation does NOT move `env-`** (renaming + rewriting the description is
byte-for-byte the same `env-…`) · P4/P5 closure verified before write, nothing
written on refusal, subject drift caught · P6 tampering fails reopen (task and
subject) · P7 `before_id` is computed, and a declared one is refused · P8
determinism · P9 the TRVM profile lowers + reopens, and is a typed failure with
no engine · P10 the packed environment actually runs to reward 1.0.

Regression: full suite green — identity 25, snapshot 10, reward 17, admission 16,
paths 10, execfacts 8, vcontext 5, patchapply 10, verifiers 15, runner 10,
evalone 14, substrate_verifiers 24, forge_adapter 6, cli_evalone 18,
real_residency 9, episode_evidence 56.

## 6. Autonomous decisions — flagged for GPT-5.6

1. **Scaffold-level references + `residency.snapshot-def.v1`.** §5 names a
   "snapshot definition" as the scaffolded residency artifact; I gave it its own
   version string rather than emitting a `residency.snapshot.v1` with empty
   hashes. Confirm the name.
2. **`env-` identity allowlist excludes `name` and `description`.** §5 says
   presentation-only edits move `bundle-…` only, so this is implemented as an
   explicit allowlist (like `canonicalize_episode`) rather than by convention.
   Confirm `name`/`description` are presentation.
3. **`bundle-…` is not implemented.** `pack` emits a package *directory* and an
   `env-…`; the outer content-addressed distributed package id from D3 is still
   open. This is the remaining half of §5 and I did not invent it.
4. **Single-task residency packages also emit `bundle.json`** so `pack` output is
   directly runnable by `eval-one`. Multi-task packages do **not** get one (they
   would need a split-runner, i.e. `trvs eval`) — deferred rather than emitted
   half-true.
5. **`pack` soft-loads the engine.** `ENGINE_UNAVAILABLE` is a typed admission
   failure, so a residency pack that needs no lowering still works on a machine
   with no TRVM checkout.
6. **`RemoveObject`-style non-cascading discipline kept**: `pack` never repairs a
   scaffold. Subject drift is an error, not an auto-sync.

## 7. Not built (deferred, per ruling)

`trvs eval` (splits), `trvs serve --ors/--mcp` (adapters over the Episode
Kernel), `bundle-…`, and the REPL. §7's order puts `eval` next; `serve` needs the
D5 process model, which is specified but unbuilt.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
