# RFC — TRAAVIIS environment artifacts & episode kernel

**Status.** Rulings received (GPT-5.6, 2026-07-23). This RFC captures the
decisions D1–D6 that were posed in the prior `HANDOFF_TRAAVIIS_ENV.md`, freezes
the artifact ladder and identity domains, and states the mutation laws each new
construct must satisfy. It is the specification that `trvs pack / serve / eval`
implement over the frozen Forge/TRVM identity spine. No construct here invents
identity inside the CLI; each is content-addressed (or explicitly not) below it.

---

## 0. Frozen constraints (unchanged, do not weaken)

- **`sem-…`** = `Hash(IR + policies)`. Presentation never enters it; a rotor
  value or a rewire does.
- **`scen-…`** = run inputs (digest domain), deliberately **out** of `sem-…`.
- A committed world change leaves the ScenarioDigest invariant; only the
  ReplayBundleID moves.
- Films are **checked by every applicable verifier** (`ic_ref == ic32 ==
  oracle`, byte-for-byte, where each applies), never asserted. A verifier that
  cannot apply reports `not_applicable`.
- Forge already has `forge.bundle.v2` (content-addressed, self-sufficiency /
  closure / identity laws). The environment layer sits **over** it.

---

## 1. The artifact ladder (frozen)

Eight levels, each answering exactly one question:

| id          | question                    | identity domain                         |
| ----------- | --------------------------- | --------------------------------------- |
| `sem-…`     | same world?                 | `Hash(IR + policies)`                   |
| `scen-…`    | same initialization?        | run inputs (digest domain)              |
| `rew-…`     | same scoring rubric?        | canonical reward spec (new, §2)         |
| `task-…`    | same assignment?            | `{scen-… , rew-… , termination}` (§3)   |
| `film-…`    | same behavior?              | recorded trajectory                     |
| `episode-…` | same evaluated outcome?     | `{film-… , rew-…} → receipt` (§4)       |
| `env-…`     | same environment release?   | manifest over world/tasks/rewards/splits (§5) |
| `bundle-…`  | same distributed package?   | `env-…` + presentation + docs (§5)      |

---

## 2. D1 — Reward is a first-class `rew-…` artifact

**Ruling:** reward is **neither** the world's meaning (it does not enter
`sem-…`) **nor** an arbitrary run input (not folded into `scen-…`). It is a
**declared, content-addressed rubric** with its own digest domain.

`RewardSpecV1` (`reward_spec_version = "forge.reward.v1"`):

```json
{ "reward_spec_version": "forge.reward.v1",
  "reward_id": "rew-…",              // = rew- + sha256(canonical bytes)
  "applies_to": { "world_semantic_id": "sem-…" },
  "signals": [ … ],                  // pure functions over a film
  "aggregation": "terminal" }        // terminal | process (process ships later)
```

**Mutation laws.**

- `rew-…` is a **pure content hash** of canonical bytes; reordering signals or
  relabelling them collapses to the same `rew-…` (canonicalization first).
- A `rew-…` **does not enter** any `sem-…` or `scen-…`. Changing the rubric
  moves no world identity and no scenario digest.
- Re-scoring the *same* `film-…` under a different `rew-…` produces a new
  `episode-…` but the **`film-…` is invariant** — the trajectory did not change.

## 3. D2 — Task is first-class; splits are manifest sets

**Ruling:** a **task is a first-class `task-…` artifact**, not a thin label. It
binds `{initial scenario, reward binding, termination}`:

```json
{ "task_spec_version": "forge.task.v1",
  "task_id": "task-…",
  "scenario_id": "scen-…",
  "reward_id": "rew-…",
  "termination": { … } }             // step budget / goal predicate
```

**Splits** (`train` / `dev` / `test`) are **named sets of `task-…` ids** in the
environment manifest — they carry **no independent identity**. Split membership
is part of `env-…` (§5), so moving a task between splits moves `env-…`, nothing
below it.

**Mutation laws.**

- `task-…` = `task- + sha256(canonical {scenario_id, reward_id, termination})`.
- Two tasks that reference the same `scen-…` + `rew-…` + termination collapse to
  the same `task-…`.
- Reassigning a task's split changes `env-…` only; the `task-…` is invariant.

## 4. D4 — Episode / reset / replay / verification

**Episode** = one fold of a scenario to termination under a task, scored by its
`rew-…`, emitting an `episode-…` **receipt**:

```json
{ "episode_id": "episode-…",
  "world_id": "sem-…", "scenario_id": "scen-…",
  "task_id": "task-…", "reward_id": "rew-…", "film_id": "film-…",
  "reward": 1, "finished": true,
  "verification": { "reference": true, "native": true, "oracle": "not_applicable" } }
```

**Ruling — `reset` ≠ `replay`.** These are different operations and must not be
conflated:

- **`reset`** *reconstructs the initial state* of a scenario with **no actions
  applied** — a fresh episode at epoch 0. It reproduces the same `sem-…` +
  `scen-…`, but it is *not* a re-fold of a recorded trajectory.
- **`replay`** *reapplies a recorded action stream* (a `film-…`) and asserts it
  reproduces byte-for-byte. This is the existing `trvs replay` semantics.

**Episode Kernel (internal, neutral):**

```text
start · observe · step · reset · finalize
```

- `observe` is a **label-free projection** of current claim/world state (the
  `_digest_domain`), so exposing it leaks nothing into identity.
- `step` applies one agent action, extending the epoch-input stream by one.
- Native/oracle agreement is checkable **after the fact** on the recorded film;
  an interactive, agent-driven run records the film and then verifies it with
  every applicable verifier.

**Mutation laws.**

- `episode-…` = `episode- + sha256(canonical {film_id, reward_id, scoring})`.
- The **verification map is total**: every verifier key is `true`, `false`, or
  `not_applicable`. Coverage is never silently dropped.
- Re-scoring moves `episode-…`, never `film-…` (restated from §2).

## 5. D3 + D5 — `env-…` vs `bundle-…`, and the serve process model

**D3 ruling — two layers.**

- **`env-…` (`traaviis.environment.v1`)** is the *environment manifest*: it fixes
  the **world, tasks, rewards, action/observation profiles, and split
  membership**. It **embeds** a closed `forge.bundle.v2` (world + scenarios) and
  adds `{tasks, rewards, splits, profiles}` around it.
- **`bundle-…`** is the *distributed package*: it carries presentation, docs, and
  screenshots and **may change without moving `env-…`**.

Closure set for `env-…` = `{active world} ∪ {scenario digests} ∪ {reward specs}
∪ {task defs}`. `env-…` is derived from the canonical manifest bytes.

**D5 ruling — serve process model.** One `trvs serve` hosts **one bundle**,
exposing the ORS adapter (HTTP) and MCP adapter over the **same in-process
Episode Kernel**. Sessions are **ephemeral, in-memory** (mirroring Spinner
Bench's runtime jobs); the durable artifact is the **film**. There is **no
single global lock** serializing every session — sessions are independent; only
the shared engine seam is guarded (mirror the `_PIPELINE_LOCK` discipline where a
folded call touches shared state, not the whole session lifetime).

**Mutation laws.**

- `env-…` moves iff the manifest (world / tasks / rewards / splits / profiles)
  moves. Presentation-only edits move `bundle-…` only.
- `pack` is **fail-closed**: it re-opens and re-verifies the emitted bundle —
  the world re-lowers to its declared `sem-…` and every task / reward / scenario
  reference resolves inside the closure — **before** reporting success.

## 6. D6 — `init` templates + `pack` admission

**Ruling.**

- **`init`** is **pure file scaffolding** — a seed world source plus a manifest
  skeleton. It invents no identity. Templates: `blank-spinner` (minimum), later
  `courier` / `factory`.
- **`pack`** must **verify closure + re-lower identity before writing** (reuse
  `verify_bundle_closure` and the identity re-lower from Forge), then re-open and
  re-verify the emitted artifact (§5). A bundle that fails any law is rejected
  loudly, never written half-formed.

---

## 7. What this unblocks

With D1–D6 ruled, the following can be built over the frozen spine without
inventing identity in the CLI:

- `trvs init` — scaffolding against the `env-…` schema above.
- `trvs pack` — build + closure-verify + identity re-lower + re-open.
- `trvs serve --ors` / `--mcp` — adapters over the Episode Kernel (§4).
- `trvs eval` — run an agent over a split, emit an `episode-…` per task, score.

The next engineering step is to implement each construct **with mutation-law
tests first** (the laws in §2–§6), exactly as the WRL identity spine was built.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
