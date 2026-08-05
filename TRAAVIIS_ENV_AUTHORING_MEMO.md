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
identity *key* at any depth **and** every emitted byte for an id-shaped literal,
so a future template cannot quietly bake one in — including in prose.

The literal half fails **closed**. It used to be a `<prefix>-[0-9a-f]{8,}` regex,
which matches a well-formed digest and *nothing else*: a rung-prefixed token it
could not parse — `snap-abcdef12ZZZ`, `rew-ABCDEF1234567890`, or the same id
under a changed grammar (`episode-jcs1-…`) — produced no match, and a guard that
finds no id reports no violation, so a typo and a scheme change both read as
"clean". `scaffold.id_tokens` now recognises the **rung prefix first** (from the
one `ID_RUNGS` list, which `test_scaffold`'s L1c pins against the ids
`identity.py` actually mints) and then asks whether the remainder is a digest.
Three verdicts: `id`, `malformed` (a known rung whose remainder does not parse)
and `unknown` (a digest under a rung nobody should have minted). All three are
violations. The elision form `snap-…` is deliberately *not* a token — writing
"an id goes here" asserts none.

### What the guard does not report, and why that is written down

The first version of this rewrite claimed the three verdicts "leave no token
that is both id-shaped and unreported". That sentence was false in **both**
directions, and each direction is a way a guard stops guarding:

* It was **narrower than the regex it replaced**. A lookbehind refused to begin
  a token after `-` or `.`, so `prev-episode-42d0bb07e5f83e9e` and
  `run.episode-42d0bb07e5f83e9e` — which the old `\b`-anchored regex reported —
  came back as `[]`. A real, well-formed id, completely unreported. The
  lookbehind was standing in for something genuine (`traaviis.finding-
  completeness-impl.v1` is a declared verifier version present in every
  `ComparisonV1`, and reading it as the `finding` rung failed `test_compare`'s
  C20), but it paid for that with coverage and no law recorded the trade.
* It was **wider on prose**. Every hyphenated phrase whose first word happened
  to be a rung name — `env-vars`, `task-oriented`, `patch-apply`, `sem-ver`,
  `bundle-path` — was reported as a malformed id. A guard that cries wolf is
  switched off by the next person to trip it, which is fail-open by a slower
  route. Swept over this repository plus its build artifacts and sealed bundles
  (~56,000 documents), the rewrite produced **419** non-`id` verdicts where the
  current guard produces **29**. The ~60 distinct tokens it dropped are all
  prose or identifiers — `episode-bundle`, `snap-fixture`, `sem-ver`,
  `patch-fail` — including **`task-b`, which appears in real packed `env.json`
  and `TRAAVIIS_BUNDLE.json` documents**: the rewrite called a task *filename* a
  malformed id. What survives as `malformed` is `*-PLACEHOLDER`,
  `snap-abcdef12ZZZ`, `rew-ABCDEF1234567890` and `episode-jcs1-…` — every one an
  id-shaped token, and none of them prose.

The first defect is not visible as a count, and saying so is part of the record:
committed text almost never writes an id inside a compound, which is exactly why
nobody noticed it was no longer being read. It is demonstrated instead by
planting — `prev-episode-<64 hex>` written into a real scaffolded template and
into a real `ComparisonV1`, where the legacy regex reports it, the rewrite
reports nothing, and the current guard reports it again.

The discrimination that keeps both properties is the **remainder's shape**, with
position gating only the `malformed` verdict and never the `id` one:

| | reported |
| --- | --- |
| a well-formed digest, anywhere — `prev-episode-<hex>`, `run.episode-<hex>` | **yes**, as `id` |
| a rung + a remainder bearing a digit, an uppercase letter, or a run ≥ 16 | **yes**, as `malformed` |
| a rung + lowercase words of ordinary length — `env-vars`, `episode-nonsense` | no |
| a rung *embedded* in a compound name without a digest — `traaviis.finding-completeness-impl.v1`, `traaviis-snap-01uu24ob` | no |

The third row is a **stated trade, not a discrimination**: `nonsense` and
`oriented` are both eight lowercase letters with the same hex-letter density,
and no lexical rule separates a typo'd id whose remainder happens to be
word-shaped from an English phrase. What bounds the silence is that one edit in
any direction — a digit, an uppercase letter, a longer run, an actual digest —
puts the token back in scope. A mangled sha256 keeps its digits; a word has
none. Both silences are pinned by `test_scaffold`'s **L1g**, and the boundary by
**L1e**, so widening either has to move a law rather than a comment.

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
