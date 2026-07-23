# TRAAVIIS — Architecture & the road to the environment surface

> **Thesis.** TRAAVIIS is the local-first authoring, evaluation, and
> verification toolchain for **deterministic agent environments**. Not another
> coding agent, not a model router, not an RL cloud, not the whole TRVM research
> workbench. Its value is a single loop: *write the world → run the episode →
> compute objective rewards → verify → replay, compare, and publish.*
>
> **Write the wall. Run the world. Keep the proof.**

This document describes the product boundary, what ships today, and the road to
the environment surface (`serve`/`pack`/`eval`). It supersedes the earlier
"terminal harness engineer" thesis — that positioning is retired.

---

## 1. The product boundary

The seams are frozen on purpose. `trvs` carries **no world semantics**; it
packages capabilities the layers below already provide, for someone who does not
know the internal history of TRVM.

| layer            | responsibility                                          |
| ---------------- | ------------------------------------------------------- |
| **TRAAVIIS**     | the product — CLI, environment SDK, evaluator, packaging, visual workbench |
| **trvs**         | the command-line interface                              |
| **WallRiderLang**| the language for worlds, actors, tasks and rules        |
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

The ORS wire surface (`list_tasks · session · call_tool → reward · finished`)
and the MCP primitives (`tools · resources · prompts`) are both *projections* of
the same kernel. The kernel owns the semantics; adapters only translate.

### 3a. The artifact ladder

TRAAVIIS freezes an eight-level artifact ladder, each level answering exactly one
question, so two researchers never argue about what agreed and what did not:

| id          | question                        | domain                        |
| ----------- | ------------------------------- | ----------------------------- |
| `sem-…`     | was it the same world?          | Hash(IR + policies)           |
| `scen-…`    | same initialization?            | run inputs, out of identity   |
| `rew-…`     | same scoring rubric?            | declared reward spec          |
| `task-…`    | same assignment?                | scenario + reward + terminate |
| `film-…`    | same behavior?                  | the recorded trajectory       |
| `episode-…` | same evaluated outcome?         | film + rubric → receipt       |
| `env-…`     | same environment release?       | world + tasks + rewards + splits |
| `bundle-…`  | same distributed package?       | env + presentation + docs     |

Re-scoring the *same* film under a different rubric changes the `episode-…`
receipt but never the `film-…` — the trajectory did not change.

### 3b. The bundle — `traaviis.environment.v1`

`trvs pack` separates *what an environment means* from *how it is shipped*. The
**environment manifest** (`env-…`) fixes the world, tasks, rewards, action /
observation profiles, and split membership; the outer **package** (`bundle-…`)
carries presentation, docs, and screenshots and may change without moving
`env-…`. It layers over the existing `forge.bundle.v2` (world + scenarios,
already closed), adding `{tasks, rewards, splits, action/observation profiles}`.

Three laws (mirroring the existing Forge bundle discipline):

- **self-sufficiency** — the object set is derived from the doc.
- **closure** — the active world re-lowers to its declared `sem-…` and every
  task / reward / scenario reference resolves inside the closure, or import
  fails loudly.
- **identity** — `pack` re-opens and re-verifies the emitted bundle before
  reporting success.

An episode emits a receipt:

```json
{ "world_id": "sem-…", "scenario_id": "scen-…", "task_id": "task-…",
  "reward_id": "rew-…", "film_id": "film-…", "episode_id": "episode-…",
  "reward": 1, "finished": true,
  "verification": { "reference": true, "native": true, "oracle": "not_applicable" } }
```

Every verifier field is `true`, `false`, or `not_applicable` — coverage is never
silently dropped.

### 3c. Evaluation before training

The first job is not a trainer. `trvs eval` runs an agent over a split and scores
every episode; a comparison view diffs two runs. Because every episode is
verified and content-addressed, the numbers are reproducible and the films are
re-checkable. Training frameworks drive rollouts *through* the ORS adapter later.

### 3d. Later: verified process rewards

Because TRVM films observe each transition, TRAAVIIS can eventually emit
intermediate, verifier-grounded rewards (e.g. `+0.2 preserved safety invariant`,
`-0.3 duplicated forbidden resource`) rather than only a terminal reward. This
depends entirely on verifier reliability, so it ships **after** deterministic
terminal rewards and complete episodes are solid.

---

## 4. Sequencing

- **v0.7-5 (Spinner Bench)** — close the Public Alpha: freeze presentation,
  package source, document limits, publish reproducible acceptance. Then freeze
  Bench feature work.
- **TRAAVIIS v0.1** — `doctor · init · id · inspect · run · verify · replay ·
  pack`; ship Golden Spinner as the first bundle.
- **TRAAVIIS v0.2** — the Episode Kernel + `trvs serve --ors`; ship the
  Courier/Factory world so the kernel is exercised by a stateful, long-horizon
  environment *before* `eval` is called complete.
- **TRAAVIIS v0.3** — `eval · compare · reports` over the kernel; MCP adapter.
- **TRAAVIIS v0.4** — an ORS/TRL example, process-reward hooks, environment
  publishing; ship WallRider/Graffiti world.

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
mutation laws each construct must satisfy. That RFC, not this document, is the
next thing to turn into engine code.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
