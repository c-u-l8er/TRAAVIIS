# RFC — TRAAVIIS environment artifacts & episode kernel

**Status.** Rulings received (GPT-5.6, 2026-07-23). This RFC captures the
decisions D1–D6 that were posed in the prior `HANDOFF_TRAAVIIS_ENV.md`, freezes
the artifact ladder and identity domains, and states the mutation laws each new
construct must satisfy. It is the specification that `trvs pack / serve / eval`
implement over the frozen Forge/TRVM identity spine. No construct here invents
identity inside the CLI; each is content-addressed (or explicitly not) below it.

> **Substrate-neutral update (2026-07-23).** TRAAVIIS evaluates over multiple
> substrates, not only TRVM. The **shared** ladder (`task-…`, `rew-…`,
> `episode-…`, and the `SubstrateProfileV1` that prepares the subject) is common;
> the **observable execution record** is substrate-specific. A **`film-…` is the
> TRVM-only** deterministic-execution artifact; the substrate-neutral record is a
> **`trace-…`** (a `film-…` *is* the TRVM case of a `trace-…`). The `episode-…`
> receipt carries a `substrate_profile` and references whichever
> substrate-specific evidence exists. See `RFC_EVIDENCE_RESIDENCY.md` for the
> first non-TRVM substrate (`residency.repository.v1`: `snap-…` · `trace-…` ·
> `finding-…` · `patch-…`).

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
| `trace-…`   | same behavior?              | observable execution record (TRVM case = `film-…`) |
| `episode-…` | same evaluated outcome?     | `{substrate_profile, trace-…, rew-…} → receipt` (§4) |
| `env-…`     | same environment release?   | manifest over world/tasks/rewards/splits (§5) |
| `bundle-…`  | same distributed package?   | `env-…` + presentation + docs (§5)      |

---

> **Namespace ruling.** The **shared** evaluation artifacts live in the
> **`traaviis.*`** namespace, not `forge.*`. Forge owns TRVM compilation and
> identity (`sem-…`, `scen-…`, `film-…`); TRAAVIIS owns the substrate-neutral
> evaluation ladder: `traaviis.task.v1`, `traaviis.reward.v1`,
> `traaviis.episode.v1`, `traaviis.substrate-profile.v1`.

## 2. D1 — Reward is a first-class `rew-…` artifact

**Ruling:** reward is **neither** the subject's meaning (it does not enter a
world's `sem-…`) **nor** an arbitrary run input (not folded into `scen-…`). It is
a **declared, content-addressed rubric** with its own digest domain, and its
signals are **bound to a substrate profile** — a rubric is not a "function over a
film" in general, because a Residency rubric scores evidence, not a fold.

`RewardSpecV1` (`reward_spec_version = "traaviis.reward.v1"`):

```json
{ "reward_spec_version": "traaviis.reward.v1",
  "reward_id": "rew-…",              // = rew- + sha256(canonical bytes)
  "substrate_profile": "residency.repository.v1",
  "signals": [ "citations", "patch", "tests", "identity" ],
  "weights": { "citations": 0.25, "patch": 0.20, "tests": 0.30,
               "identity": 0.15, "finding_completeness": 0.10 },
  "floors": [ … ],                   // hard caps (see Evidence Residency §7)
  "aggregation": "terminal" }        // terminal | process (process ships later)
```

The TRVM form binds the other substrate:

```json
{ "reward_spec_version": "traaviis.reward.v1",
  "substrate_profile": "trvm.world.v1",
  "signals": [ "terminal_claim", "film_property" ] }
```

**Mutation laws.**

- `rew-…` is a **pure content hash** of canonical bytes; reordering signals or
  relabelling them collapses to the same `rew-…` (canonicalization first).
- A `rew-…` **does not enter** any `sem-…`, `scen-…`, `snap-…`, or `trace-…`.
  Changing the rubric moves no subject identity and no observable record.
- Re-scoring the *same* `trace-…` (a `film-…` for TRVM) under a different `rew-…`
  produces a new `episode-…`, but the **`trace-…` is invariant** — behaviour did
  not change.

## 3. D2 — Task is first-class and substrate-neutral; splits are manifest sets

**Ruling:** a **task is a first-class `task-…` artifact**, not a thin label, and
it must be able to describe a Residency task as well as a TRVM one. A `scen-…` +
`rew-…` + termination triple is **too narrow** — it cannot carry the frozen
subject, the instructions, the verifier requirements, the identity-movement
policy, or the agent execution policy. The neutral shape:

```json
{ "task_spec_version": "traaviis.task.v1",
  "task_id": "task-…",
  "substrate_profile": "residency.repository.v1",
  "subject": { "snapshot_id": "snap-…" },
  "instructions": { "objective": "Identify one WRL spec/impl inconsistency…" },
  "reward_id": "rew-…",
  "verifier_plan": { "required": ["citations","patch","tests","identity"],
                     "not_applicable": ["native","oracle"] },
  "identity_policy": { "must_remain": ["demo_world_semantic_id"],
                       "may_move": ["documentation"] },
  "termination": { "mode": "one_shot" },
  "agent_run_policy": { "timeout_seconds": 300, "network": "disabled" } }
```

The TRVM form uses a world subject instead of a snapshot:

```json
{ "substrate_profile": "trvm.world.v1",
  "subject": { "world_id": "sem-…", "scenario_id": "scen-…" } }
```

**Splits** (`train` / `dev` / `test`) are **named sets of `task-…` ids** in the
environment manifest — they carry **no independent identity**. Split membership
is part of `env-…` (§5), so moving a task between splits moves `env-…`, nothing
below it.

**Mutation laws.**

- `task-…` = `task- + sha256(canonical TaskSpecV1 without task_id)` — and
  **identity includes the instructions**: changing what the agent was asked to
  do **must move `task-…`**.
- Two tasks with byte-identical canonical specs collapse to the same `task-…`.
- Reassigning a task's split changes `env-…` only; the `task-…` is invariant.

## 4. D4 — Episode / reset / replay / verification

**Episode** = **one evaluation run over a frozen substrate subject**, scored by
its `rew-…`, emitting an `episode-…` **receipt**. (For TRVM the run is a fold of
a scenario; for Residency it is a controlled agent run over a snapshot — the
receipt is the same shape.) The **durable result** is *trace + outputs + episode
receipt* (not "the film" — that is only the TRVM trace):

```json
{ "episode_id": "episode-…",
  "episode_version": "traaviis.episode.v1",
  "substrate_profile": "trvm.world.v1",
  "task_id": "task-…", "reward_id": "rew-…",
  "subject": { "world_id": "sem-…", "scenario_id": "scen-…" },
  "trace_id": "film-…",
  "outputs": {},
  "verification": { "reference": "pass", "native": "pass", "oracle": "not_applicable" },
  "verifier_versions": { "reference": "1", "native": "1" },
  "reward": 1, "status": "completed", "validity": "valid",
  "replayability": "exact",
  "execution_facts": {} }
```

The verification map uses `pass` / `fail` / `not_applicable` / `error` (see
`RFC_EVIDENCE_RESIDENCY.md` §6 — `error` is distinct from `fail`). A non-TRVM
substrate emits the same shape with a substrate-specific `trace_id` and verifier
set; e.g. `residency.repository.v1` sets `trace_id` to a `trace-…`, populates
`outputs.{finding_id, patch_id}`, and reports `native`/`oracle` as
`not_applicable`. `execution_facts` records actual toolchain versions, command
exit codes, and platform facts relevant to verification.

**Ruling — three meanings of replay.** `EpisodeReceiptV1.replayability` records
which level a receipt supports:

- **exact replay** — re-running canonical inputs reproduces the same trajectory
  and `film-…`. TRVM only.
- **action replay** — recorded tool calls are reapplied against the same frozen
  subject. A controlled runner may support this *later*.
- **verification replay** — the produced evidence and outcome are re-checked
  against the frozen subject. This is what a Residency episode promises in v1.

**Ruling — `reset` ≠ `replay`** (within a substrate that supports stepping):

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

- **`episode_id = "episode-" + sha256(canonical(EpisodeReceiptV1 without
  episode_id))`.** The whole receipt is hashed — `substrate_profile`, `task_id`,
  `reward_id`, `subject`, `trace_id`, `outputs`, `verification`,
  `verifier_versions`, `reward`, `status`, `validity`, `replayability`,
  `execution_facts`. **Excluded from identity:** wall-clock timestamps, absolute
  paths, transient process IDs, display formatting, host-specific log locations.
- The **verification map is total**: every declared verifier reports `pass`,
  `fail`, `not_applicable`, or `error`. Coverage is never silently dropped.
- Re-scoring moves `episode-…`, never the `trace-…`/`film-…` (restated from §2).
- **Verifier versions are recorded in the receipt**: changing verifier
  implementation version moves `episode-…` while the underlying evidence
  artifacts stay stable (see `RFC_EVIDENCE_RESIDENCY.md` §9).

## 5. D3 + D5 — `env-…` vs `bundle-…`, and the serve process model (DEFERRED)

> **Deferred.** `env-…`, `bundle-…`, `pack`, and `serve` do **not** block the
> first `eval-one`. They are specified here for continuity but are not built
> until one real environment runs. An **environment** is a *substrate profile + a
> closed subject/task/reward set*, generalized over substrates:
> - `trvm.world.v1` closure — forge bundle + scenarios + tasks + rewards.
> - `residency.repository.v1` closure — snapshot definition + tasks + rewards +
>   verifier/execution profiles.

**D3 ruling — two layers.**

- **`env-…` (`traaviis.environment.v1`)** is the *environment manifest*: a
  **substrate profile** plus the closed set of **subjects, tasks, rewards,
  action/observation or verifier profiles, and split membership**. For the TRVM
  substrate it embeds a closed `forge.bundle.v2`; for Residency it embeds a
  snapshot definition and execution/verifier profiles.
- **`bundle-…`** is the *distributed package*: it carries presentation, docs, and
  screenshots and **may change without moving `env-…`**.

Closure set for `env-…` = `{subject} ∪ {task defs} ∪ {reward specs} ∪
{substrate-specific profiles}`. `env-…` is derived from the canonical manifest
bytes.

**D5 ruling — serve process model.** One `trvs serve` hosts **one environment**,
exposing the ORS adapter (HTTP) and MCP adapter over the **same in-process
Episode Kernel**. Sessions are **ephemeral, in-memory** (mirroring Spinner
Bench's runtime jobs); the **durable result is the trace + outputs + episode
receipt**. There is **no single global lock** serializing every session —
sessions are independent; only the shared engine seam is guarded (mirror the
`_PIPELINE_LOCK` discipline where a folded call touches shared state, not the
whole session lifetime).

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

## 7. What this unblocks — and the build order

The **first** thing built is not `pack`/`serve`/`eval` but a single, boring,
one-shot evaluation of one Residency task (`trvs eval-one`, see
`RFC_EVIDENCE_RESIDENCY.md` §10). The build order:

1. **Canonical artifact functions + mutation-law tests only** — `snapshot_id()`,
   `finding_id()`, `patch_id()`, `trace_id()`, `reward_id()`, `task_id()`,
   `episode_id()` against the frozen mutation battery. No agent process launched.
2. A fixed **stub-agent fixture**.
3. The **controlled runner** + the four Residency verifiers.
4. `trvs eval-one` end to end.

Only after that does the deferred surface become grounded:

- `trvs init` — scaffolding against the `env-…` schema (§5).
- `trvs pack` — build + closure-verify + identity re-lower + re-open.
- `trvs serve --ors` / `--mcp` — adapters over the Episode Kernel (§4).
- `trvs eval` — run an agent over a split, emit an `episode-…` per task, score.

Every construct is implemented **with mutation-law tests first** (the laws in
§2–§6), exactly as the WRL identity spine was built.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
