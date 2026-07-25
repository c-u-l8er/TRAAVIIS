# TRAAVIIS — Architecture & the road to the environment surface

> **Thesis.** TRAAVIIS is the local-first toolchain for **evidence-grade agent
> evaluation**. Not another coding agent, not a model router, not an RL cloud,
> not the whole TRVM research workbench. Its core loop is: *freeze the subject →
> run the evaluation → score explicit evidence → verify → preserve a
> re-verifiable receipt.* TRVM supplies the strongest exact-replay substrate
> under it; Evidence Residency is its first repository-evidence substrate.
>
> **Write the wall. Run the world. Keep the proof.**

This document describes the product boundary, what ships today, and the road to
the environment surface (`serve`/`pack`/`eval`). It supersedes the earlier
"terminal harness engineer" thesis — that positioning is retired.

---

## 1. The product boundary

The seams are frozen on purpose. `trvs` carries **no substrate semantics**; it
packages capabilities the layers below already provide, for someone who does not
know the internal history of TRVM.

| layer            | responsibility                                          |
| ---------------- | ------------------------------------------------------- |
| **TRAAVIIS**     | the product — CLI, environment SDK, evaluator, packaging, visual workbench |
| **trvs**         | the command-line interface                              |
| **TaskSpecV1**   | the substrate-neutral assignment and evaluation contract |
| **WallRiderLang**| the language for TRVM worlds, actors and world rules    |
| **Forge**        | the compiler, identity and artifact pipeline            |
| **TRVM**         | the deterministic execution substrate                   |
| **Spinner Bench**| the reference workbench and conformance laboratory      |

Two rules follow directly:

1. **Spinner Bench is not TRAAVIIS.** It remains the place where the
   language/runtime is exercised, visually inspected, and proven. TRAAVIIS
   *consumes* those capabilities and packages them.
2. **`trvs` invents no state semantics.** Every command is a thin call over the
   Forge engine's stable public API (`traaviis.engine → forge_api`). Identity,
   lowering, folding, and verification live below the CLI and are versioned
   there.

---

## 2. What is real today

`traaviis` is a zero-dependency Python package exposing the `trvs` command over
`traaviis.engine`. Seven commands fold real worlds:

| command        | contract                                                        |
| -------------- | --------------------------------------------------------------- |
| `trvs doctor`  | reports engine dir, API version, `ic_ref`/`ic32`/oracle status  |
| `trvs id`      | re-lowers source → `SemanticArtifactID` (pure identity)         |
| `trvs inspect` | lowered actors, edges, resolved static config, diagnostics      |
| `trvs run`     | folds the world through `ic_ref`; prints the per-epoch film     |
| `trvs verify`  | cross-checks `ic_ref` vs native `ic32` vs the Fixture oracle    |
| `trvs replay`  | re-folds and asserts a pinned `--expect` id / `--film` hash     |
| `trvs diff`    | folds two worlds and marks the first divergent epoch            |

**Identity model.** Presentation (position, colour, wire curve) never enters the
hash; a rotor value or a rewire does. Run inputs (the scenario) are deliberately
kept *out* of the `SemanticArtifactID` and carried as a separate `scen-…`
digest. This is the split that makes an environment content-addressable.

**Verification contract.** A film is not asserted — it is *checked by every
applicable verifier*. The reference reducer (`ic_ref`), the compiled native
reducer (`ic32`), and an independent oracle each run where their domain applies
and must agree byte-for-byte. Coverage is explicit: a verifier that cannot apply
to a world is reported `not_applicable`, never counted as a pass or a fail. The
Golden Spinner agrees 3/3; a bare Blank Spinner is a valid world for
`ic_ref == ic32` but sits outside the Fixture oracle's domain. Exit codes: `0`
agree, `1` ran-and-disagreed, `2` a verifier was unavailable. That makes any
verifying command a fail-closed gate.

---

## 3. The environment surface (roadmap)

The gap between "a verifiable world" and "an environment an agent trains
against" is task/reward/episode lifecycle. The internal contract for that
lifecycle is a **neutral Episode Kernel** — public protocols are *adapters* over
it, never runtime law. This keeps [Open Reward Standard](https://openreward.ai)
or MCP evolution from ever becoming TRVM runtime semantics.

```text
Episode Kernel   start · observe · step · reset · finalize   (internal, neutral)
        ↓
ORS adapter      first / primary public surface  →  trvs serve --ors
MCP adapter      compatibility (tools/resources/prompts)  →  trvs serve --mcp
JSONL adapter    local automation / debugging
```

The kernel carries **no substrate semantics**; each verb is substrate-defined
and may be declared *unsupported* rather than faked. `start` prepares the frozen
subject; `finalize` seals the trace and outputs, runs the verifier plan, scores
the reward, and emits the episode. `observe` / `step` / `reset` are supported
only where a substrate can honour them — **Evidence Residency v1 is one-shot and
implements only `start → finalize`.** For the TRVM profile, `observe` is a
label-free `_digest_domain` projection, `step` extends the epoch-input stream,
`reset` reconstructs `sem-… + scen-…` at epoch 0, and `finalize` checks
`ic_ref == ic32 == oracle`; those are TRVM profile bindings, not kernel law. The
ORS wire surface (`list_tasks · session · call_tool → reward · finished`) and the
MCP primitives (`tools · resources · prompts`) are both *projections* of the same
kernel. The kernel owns the semantics; adapters only translate.

### 3a. The artifact ladder

TRAAVIIS freezes a **substrate-neutral** artifact ladder. The lowest rung is the
*substrate subject*, sealed differently by each substrate; every rung above it is
shared, so two researchers never argue about what agreed and what did not:

| id / rung             | question                        | domain                        |
| --------------------- | ------------------------------- | ----------------------------- |
| **substrate subject** | was it the same subject?        | TRVM: `sem-…`+`scen-…` · Residency: `snap-…` |
| `rew-…`               | same scoring rubric?            | declared reward spec          |
| `task-…`              | same assignment?                | subject + reward + terminate  |
| `trace-…`             | same observable behavior?       | the recorded observable record |
| `episode-…`           | same evaluated outcome?         | trace + rubric → receipt      |
| `env-…`               | same environment release?       | subject + tasks + rewards + splits |
| `bundle-…`            | same distributed package?       | env + presentation + docs     |

The shared evaluation constructs (`task-…`, `rew-…`, `episode-…`, the substrate
profile) live in the `traaviis.*` namespace; substrate-specific evidence lives
below. `sem-…`/`scen-…` are **TRVM substrate identities**, not universal rungs;
`snap-…` is the Residency subject. A **`trace-…`** is the substrate-neutral
observable record; a **`film-…`** is the *TRVM case* of a `trace-…` — a
deterministic-execution artifact that does not generalize. Forge owns TRVM
compilation and identity (`sem-…`/`scen-…`/`film-…`); TRAAVIIS owns the shared
evaluation ladder.

Re-scoring the *same* trace under a different rubric changes the `episode-…`
receipt but never the `trace-…` — the recorded behavior did not change.

### 3b. The bundle — `traaviis.environment.v1`

`trvs pack` separates *what an environment means* from *how it is shipped*. The
**environment manifest** (`env-…`) fixes the substrate subject, tasks, rewards,
action / observation or verifier profiles, and split membership; the outer
**package** (`bundle-…`) carries presentation, docs, and screenshots and may
change without moving `env-…`. For the TRVM substrate it layers over the existing
`forge.bundle.v2` (world + scenarios, already closed), adding
`{tasks, rewards, splits, profiles}`.

`pack` runs against a **substrate-profile admission interface**
(`validate_subject · verify_closure · recompute_subject_identity ·
reopen_package`), not against `world` verbs; each substrate implements it (TRVM
re-lowers source → `sem-…`; Residency recomputes snapshot → `snap-…`). Three
laws (mirroring the existing Forge bundle discipline):

- **self-sufficiency** — the object set is derived from the doc.
- **closure** — the subject re-derives to its declared identity and every task /
  reward / subject reference resolves inside the closure, or import fails loudly.
- **identity** — `pack` re-opens and re-verifies the emitted bundle before
  reporting success.

**Shipped** (`traaviis/substrates.py`, `traaviis/pack.py`, `traaviis/scaffold.py`).
`init` scaffolds and derives nothing — it emits scaffold-level references
(`reward_spec`, `snapshot_def`) precisely so it never asserts a hash it did not
compute — and `pack` replaces them with recomputed ids. `env-…` is derived from
an explicit identity allowlist (`substrate_profile · subject · tasks · rewards ·
profiles · splits`), so renaming an environment or rewriting its description
moves the *package*, never `env-…`. Both substrate profiles are implemented;
a single-task residency package is emitted as a runnable eval-bundle, closing
`init → pack → eval-one`.

An episode emits a receipt:

```json
{ "episode_version": "traaviis.episode.v1", "substrate_profile": "trvm.world.v1",
  "task_id": "task-…", "subject": { "world": "sem-…", "scenario": "scen-…" },
  "reward_id": "rew-…", "trace": "film-…", "episode_id": "episode-…",
  "reward": 1, "status": "ok", "finished": true,
  "verification": { "reference": "pass", "native": "pass", "oracle": "not_applicable" } }
```

For a Residency episode the same receipt carries
`"substrate_profile": "residency.repository.v1"`, a `trace-…` in place of the
`film-…`, and `citations`/`patch`/`tests`/`identity` verifier fields. Every
verifier field is one of `pass`, `fail`, `not_applicable`, or `error` — coverage
is never silently dropped, and `error` (substrate unavailable) stays distinct
from `fail` (the work was wrong).

### 3c. Evaluation before training

The first job is not a trainer, and the first interface is not a batch. It is a
one-shot **`trvs eval-one task.json --agent-command …`** over a single frozen
subject. Batch `trvs eval` follows once eval-one is boring. Because every episode
is verified and content-addressed, the numbers are reproducible and the traces
are re-checkable. Training frameworks drive rollouts *through* the ORS adapter
later.

**Shipped** (`traaviis/evalsplit.py`). `trvs eval PKG --split NAME` runs an agent
over a split and emits one `episode-…` per task. It adds **no rung to the ladder**
— §1 stops at `env-…`/`bundle-…`, so a run over a split gets an *index*
(`traaviis.evaluation.v1`) naming the `env-…`, the split, and the episode ids,
and carries no id of its own. The admission order mirrors `pack`: reopen the
package (every id re-derived from the written bytes) → resolve the split → bind
the subject tree to its snapshot → *only then* launch anything. A task that
fails is recorded as a failed episode and the split continues; a refusal exits 2
rather than reporting a score of zero.

*Canonical form.* An environment is a closed **set** of tasks, rewards and split
members (§3), and a set has exactly one written order — so `pack` sorts tasks and
rewards by their derived ids and split members by task id, and reordering an
author's source list moves no identity. Producing that order is not enough:
`open_package` **re-checks** it, because a hand-built manifest that is internally
consistent but unsorted would otherwise reopen cleanly and give one environment
as many identities as its task list has permutations. A noncanonical manifest is
`MANIFEST_NONCANONICAL`, checked before the `env-…` comparison so the failure
reads as a malformed *form* rather than a confusing identity mismatch.

*Two outcomes, not one.* Each index entry carries `status`/`reward` — what the
evaluation found — and a separate `persistence {requested, status, error}` —
whether the evidence the caller asked to keep was kept. Conflating them once let
a run report `ok` for an episode whose bundle had failed to write: a score with
no retained proof, reported as if the proof existed. The totals count both, and
the exit code reads them in precedence — persistence failure 2, disagreement 1,
otherwise 0. `OSError` is caught alongside `EpisodeBundleError` deliberately: a
read-only output directory is the most ordinary way retention fails, and it must
be a recorded outcome for one task rather than an exception that abandons the
rest of the split.

### 3c-bis. Verifier wiring — `VerifierRegistryV1`

Three things that are easy to conflate are kept distinct:

| layer                      | function of                | where it lives              |
| -------------------------- | -------------------------- | --------------------------- |
| declared plan              | the task alone             | `wiring.declared_signals`   |
| available implementations  | task + runtime/registry    | `VerifierRegistryV1`        |
| sealed history             | what actually answered     | `receipt.verifier_versions` |

The registry is built **once per command** from the engine that command selected,
so every task in a split is answered by the same verifiers bound to the same
Forge checkout — and `real_adapter(forge_api)` takes that engine explicitly
rather than independently rediscovering a possibly different one. An
unreachable engine leaves `identity` declared but unwired, reported as a note; it
is never silently dropped and never faked into a `pass`. Because the versions are
sealed into `episode-…`, an engine upgrade honestly moves the episode id instead
of quietly re-scoring the same one.

### 3c-ter. Test plans — `traaviis.test-plan.v2`

V2 replaced host-specific `argv` with a logical `tool` + `args` under a named
`toolchain_profile`, so `task-…` is host-independent and the resolved binary is
an execution fact. It further lets each command declare per-phase
`allowed_exit_codes`, both defaulting to `[0]` — exactly the V1 rule, so an
undeclared plan keeps its old meaning. This is what makes a **repair task**
expressible: under a hardcoded baseline-must-exit-0 rule, a task could not
require a test that fails before the fix and passes after, which is the ordinary
shape of a real bug report. The verdict asymmetry is deliberate — the baseline
judges the *fixture* (a miss is `error`, the task is inadmissible), the patched
run judges the *candidate* (a miss is `fail`) — and every record states the
`expected_exit_codes` it was judged by, so the evidence names its own rule.

### 3d. Later: verified process rewards

Because TRVM films observe each transition, TRAAVIIS can eventually emit
intermediate, verifier-grounded rewards (e.g. `+0.2 preserved safety invariant`,
`-0.3 duplicated forbidden resource`) rather than only a terminal reward. This
depends entirely on verifier reliability, so it ships **after** deterministic
terminal rewards and complete episodes are solid.

---

## 4. Sequencing

The thesis is **evidence-grade environments for evaluating agents**. The
sequencing builds *one* end-to-end evaluation environment before widening the
platform. Flagship worlds ship in this order:

**Golden Spinner → Evidence Residency → Courier → WallRider.**

- **v0.7-5 (Spinner Bench)** — close the Public Alpha: freeze presentation,
  package source, document limits, publish reproducible acceptance. Then freeze
  Bench feature work. Bench proceeds *independently* and does not block the
  evaluation surface below.
- **TRAAVIIS v0.1** — the shared artifact ladder as pure identity functions
  (`snapshot_id · finding_id · patch_id · trace_id · reward_id · task_id ·
  episode_id`) with mutation-law tests, then `trvs eval-one` over
  **Evidence Residency** — an agent inspects a frozen repository, finds a real
  spec/impl inconsistency, cites evidence, proposes the smallest patch, runs the
  declared checks, and returns a structured finding + a re-verifiable receipt.
  Ship Golden Spinner as the first TRVM bundle alongside. See
  `RFC_EVIDENCE_RESIDENCY.md`.
- **TRAAVIIS v0.2** — the Episode Kernel + `trvs serve --ors`; ship the
  **Courier**/Factory world so the kernel is exercised by a stateful,
  long-horizon environment *before* batch `eval` is called complete.
- **TRAAVIIS v0.3** — batch `eval · compare · reports` over the kernel; MCP
  adapter.
- **TRAAVIIS v0.4** — an ORS/TRL example, process-reward hooks, environment
  publishing; ship the **WallRider**/Graffiti world.

---

## 5. What is deliberately not built

Model routing · generic sub-agent orchestration · another chat interface · a
cloud GPU training service · a giant environment marketplace · a complete game
engine · plugin systems before the bundle/session API is stable · a complex
interactive REPL · automatic LLM world generation as the core proposition.

LLM-assisted authoring (`trvs propose "add a locked gate opened by two
signals"`) can be useful later — but the model must produce a *reviewable graph
edit*, and the deterministic system stays authoritative.

---

## 6. The environment RFC (rulings received)

The environment surface introduces genuinely new semantic/runtime constructs.
Those were ruled below `trvs`, not invented in it. The rulings — reward as a
first-class `rew-…` artifact, tasks as first-class with splits as manifest sets,
`env-…` vs `bundle-…`, the episode/reset/replay contract (`reset` reconstructs
initial state, `replay` reapplies a recorded action stream — they are *not* the
same), one server / many sessions with no global lock, and `init`/`pack`
admission laws — are captured in `RFC_TRAAVIIS_ARTIFACTS.md`, together with the
mutation laws each construct must satisfy.

Two RFCs, not this document, are the next things to turn into engine code:

- **`RFC_TRAAVIIS_ARTIFACTS.md`** — the substrate-neutral shared ladder
  (`traaviis.*`): `TaskSpecV1`, `RewardSpecV1`, `SubstrateProfileV1`,
  `EpisodeReceiptV1`, and the `env-…`/`bundle-…`/`pack`/`serve` surface (the
  latter marked DEFERRED behind the first environment).
- **`RFC_EVIDENCE_RESIDENCY.md`** — the first flagship substrate
  (`residency.repository.v1`): `SnapshotV1`, agent outputs
  (`FindingV1`/`PatchV1`/`TraceV1`), the four verifier states and their reward
  behavior, `AgentRunPolicyV1`, and the `trvs eval-one` one-shot flow. The first
  code proves the pure identity functions with mutation-law tests only — it does
  not launch an agent.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
