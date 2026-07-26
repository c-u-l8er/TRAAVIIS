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

## 0. Frozen constraints — **TRVM substrate** (`trvm.world.v1`), do not weaken

These are the identity constraints of the **TRVM world substrate**. They are
*substrate constraints*, not global ladder law — a non-TRVM substrate (e.g.
`residency.repository.v1`) seals its subject differently (a `snap-…`, not a
`sem-…` + `scen-…`).

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

The ladder is **substrate-neutral above the subject**. The lowest rung is the
*substrate subject*, which each substrate seals in its own way; every rung above
it is shared:

| id / rung        | question                    | identity domain                         |
| ---------------- | --------------------------- | --------------------------------------- |
| **substrate subject** | same subject?          | TRVM: `sem-…` + `scen-…` · Residency: `snap-…` |
| `rew-…`          | same scoring rubric?        | canonical reward spec (shared, §2)      |
| `task-…`         | same assignment?            | `{subject, rew-… , termination}` (shared, §3) |
| `trace-…`        | same observable behavior?   | observable execution record · TRVM exact-trace subtype = `film-…` |
| `episode-…`      | same evaluated outcome?     | `{substrate_profile, trace-…, rew-…} → receipt` (shared, §4) |
| `env-…`          | same closed environment release? | manifest over subject/tasks/rewards/splits (§5) |
| `bundle-…`       | same distributed package?   | canonical package tree — `env-…` + every shipped member by path/bytes/mode (§5b) |

`sem-…` and `scen-…` are **TRVM substrate identities** (§0), not universal rungs;
`snap-…` is the Residency subject identity. Everything from `rew-…` up is
shared across substrates.

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

`RewardSpecV1` (`reward_spec_version = "traaviis.reward.v1"`). Signals are a
**canonical keyed map** — the signal identifier is the key that binds a verifier
result to a weight, so identifiers are load-bearing and there is no separate
name array to drift out of sync:

```json
{ "reward_spec_version": "traaviis.reward.v1",
  "reward_id": "rew-…",              // = rew- + sha256(canonical bytes)
  "substrate_profile": "residency.repository.v1",
  "signals": {
    "citations":            { "verifier": "residency.citations.v1", "weight": 0.25 },
    "patch":                { "verifier": "residency.patch.v1",     "weight": 0.20 },
    "tests":                { "verifier": "residency.tests.v1",     "weight": 0.30 },
    "identity":             { "verifier": "residency.identity.v1",  "weight": 0.15 },
    "finding_completeness": { "verifier": "residency.finding.v1",   "weight": 0.10 }
  },
  "floors": [ … ],                   // hard caps (see Evidence Residency §7)
  "aggregation": "terminal" }        // terminal | process (process ships later)
```

The TRVM form binds the other substrate:

```json
{ "reward_spec_version": "traaviis.reward.v1",
  "substrate_profile": "trvm.world.v1",
  "signals": {
    "terminal_claim": { "verifier": "trvm.claim.v1",    "weight": 1.0 },
    "film_property":  { "verifier": "trvm.property.v1",  "weight": 0.0 }
  } }
```

**Mutation laws.**

- `rew-…` is a **pure content hash** of canonical bytes. Reordering a
  semantically **unordered** signal map does **not** move `rew-…`
  (canonicalization sorts keys). But **renaming, rebinding, adding, removing, or
  reweighting** a signal **moves `rew-…`** — a signal identifier selects *which*
  verifier result is scored, so a relabel is a semantic change, not a cosmetic
  one.
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

**Episode Kernel (internal, neutral).** The kernel carries **no substrate
semantics**; each verb is substrate-defined and may be *unsupported*:

```text
start     prepare the frozen substrate subject
observe   return the substrate-defined agent-visible projection, when supported
step      apply one canonical action under the substrate profile, when supported
reset     reconstruct the initial subject state, when supported
finalize  seal trace + outputs, run the verifier plan, score reward, emit episode
```

A substrate that cannot support a verb declares it **unsupported** rather than
faking it. **Evidence Residency v1 is one-shot** and implements only
`start → finalize`; `observe`, `step`, and `reset` are *unsupported*, not
pretend no-ops. §4a freezes the built interface, including the two lifecycle
verbs (`list_tasks`, `close`) this sketch left implicit.

**TRVM profile note (`trvm.world.v1`).** For the TRVM substrate the kernel binds
to Forge semantics: `observe` is a **label-free projection** of current
claim/world state (the `_digest_domain`), so exposing it leaks nothing into
identity; `step` applies one agent action, extending the epoch-input stream by
one; `reset` reconstructs `sem-… + scen-…` at epoch 0 with no actions applied;
`finalize` records the `film-…` and checks `ic_ref == ic32 == oracle` where each
applies. These are **TRVM profile bindings**, not shared kernel law.

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
- **Verifier versions and canonical execution facts are recorded in the
  receipt.** Rerunning identical frozen artifacts under the *same* verifier
  versions **and** the *same* canonical execution facts leaves `episode-…`
  stable; changing either (a verifier version, or a canonical execution fact —
  toolchain / exit codes / platform) moves `episode-…` while the underlying
  evidence artifacts stay stable (see `RFC_EVIDENCE_RESIDENCY.md` §9). The
  *volatile* execution facts (wall-clock, absolute paths, PIDs) are excluded
  from the hash.

### 4a. `EpisodeKernelV1` — the extracted kernel (FROZEN)

> **Status.** **BUILT** (`traaviis/kernel.py`, battery K1–K28). Not reachable
> from the command line: it has no `trvs` verb of its own, by ruling — the
> transport slice comes after the extraction, not with it.

The kernel was extracted **before** any transport exists, which is the only
order in which the extraction means anything. A kernel written after a server is
a description of what that server needed; a kernel written before one is a
statement about what an episode *is*, which the server then has to translate.

**The interface is the ruled seven, and nothing else.**

```text
EpisodeKernelV1 {
  list_tasks()                    the closed task set of one admitted environment
  start(task_id)              →   session_id
  observe(session_id)
  step(session_id, action)
  reset(session_id)
  finalize(session_id, run_result) → EvaluationRunV1
  close(session_id)
}
```

`kernel_version` is `traaviis.episode-kernel.v1`. Support is a **declaration**
(`supported_operations`), not an accident of which method a subclass remembered
to leave alone: the base class implements none of the seven and refuses all of
them, so a subclass that forgets to override cannot pass for one that meant to.
`describe()` reports `{kernel_version, substrate_profile, operations}` so a
client can ask what it is talking to before it asks for anything.

**A `session_id` is not an identity.** It is an ephemeral in-process handle
(`session-<hex>`, freshly random per `start`), and it is deliberately **not** a
rung of the §1 ladder: `identity.py` mints nothing for it, no artifact
references it, and it never reaches an `episode-…` receipt, the canonical bytes
that are hashed, or any file written into an episode bundle. Two `start` calls
on the same task return different handles and the same `episode-…`.

**Unsupported means refused.** For `residency.repository.v1` the supported set is
exactly `list_tasks`, `start`, `finalize`, `close`; `observe`, `step` and `reset`
raise the typed refusal **`KERNEL_OPERATION_UNSUPPORTED`** and must never
succeed as no-ops. A one-shot substrate that answered `step` with "applied" would
tell an agent its action landed when nothing landed, which is a worse failure
than not offering the verb at all.

This also bounds the transport slice honestly: because `observe` is a refusal, a
*remote* client cannot read a Residency session at all, so `trvs serve --ors`
over Residency v1 exposes only `start` + `finalize` (§4b). That is a consequence
of this section, not a limitation of the server.

**The local command runner is an adapter.** `evalone.evaluate` is now
`kernel.start → runner.run_agent → kernel.finalize` and produces the pre-existing
receipt **byte for byte** — the extraction moved no byte of any `episode-…`.
The kernel never learns how an agent is invoked: it hands out `content` +
`policy` and consumes a `RunResult`. `runner.run_agent` has exactly **one** call
site in the package, `kernel.run_episode`, which is what makes "the kernel
launches nothing" a checkable claim rather than a convention.

An invalid *configuration* (a required signal no wired verifier resolves) still
opens a real session, with `runnable = False`; `finalize(session_id, None)`
scores it `status = invalid`, `reward = None`, `artifacts = None`. Refusing to
open it would convert a scored outcome into a crash, which is a different claim
about the task. `finalize` refuses a missing run result for a runnable session
(`KERNEL_RUN_RESULT_MISSING`) and an unexpected one for a session that was told
not to run (`KERNEL_RUN_RESULT_UNEXPECTED`).

**Process model.** One kernel = **one admitted environment**; many ephemeral
sessions; one shared task/reward registry; one shared engine seam. A split opens
exactly one kernel and one session per task — a kernel per task would make the
shared-registry guarantee a claim about N objects that merely happen to agree.
Sessions are independent and may be open, interleaved and finalized out of
order; **no lock is held across a session lifetime**, and no lock is held across
admission, scoring or a subprocess. This is §5's serve process model stated at
the layer that actually implements it.

**`finalize` is linearizable (FROZEN).** The session lifecycle is exactly four
states, and `closed` is not among them — a closed session is *forgotten*, not
retained:

```text
started ──claim──► finalizing ──► finalized
                              └──► finalize_failed

started / finalized / finalize_failed ──close──► forgotten
finalizing ────────────────────────────close──► KERNEL_SESSION_BUSY
```

A short lock is **not** the same as an atomic transition. Reading
`state == started` in one statement and writing `finalized` in a later one, with
the verifier plan in between, lets two concurrent callers both pass the check
and both run the plan — which for a Residency task means executing a candidate's
test suite twice. Both callers receive the *same receipt*, which is precisely
why it hides: the identity does not move and only the work doubles.

The transition is therefore a **claim**: under the table lock, look the session
up, require `state == started`, validate the run result against `runnable`, and
set `finalizing`. The expensive work then runs **outside** the lock, and the
lock is re-taken only to record `finalized` + result, or `finalize_failed`.
Exactly one caller may claim a session; every other caller is refused by name
(`KERNEL_SESSION_STATE`, or `KERNEL_SESSION_BUSY` for a `close` attempted
mid-flight). Refusing a `close` matters because removing the entry would not
stop the work — it would only make the result unattributable and let a third
caller `start` past a test suite that is still executing.

The two run-result refusals (`KERNEL_RUN_RESULT_MISSING`,
`KERNEL_RUN_RESULT_UNEXPECTED`) deliberately leave the session `started`: a
caller error that ran nothing must not consume the session's one shot.

**A failed finalization is terminal.** `finalize_failed` cannot be retried.
Verifiers and test commands have external side effects, so a retry would be a
second execution wearing the first one's name; the honest recovery is a **new
session**, which is a new episode and says so.

Linearizing one session must not serialize the kernel: two *different* sessions
may be inside the scoring work at the same time, and while one is, the table
still answers `start`, `session`, `open_sessions`, `close` and another session's
`finalize` (K25, K26 prove this under a forced rendezvous, not by reading the
source).

### 4b. `Residency Submission ORS Profile v1` — the first transport (FROZEN)

> **Status. BUILT** (`trvs serve --ors`, `traaviis/ors.py` + `ors_server.py`,
> battery O1–O30). This is the first adapter over §4a written by anyone other
> than the local runner, and it adds **no rung**: an ORS session id is the
> kernel's own `session-…` (randomness, not content) and the idempotency key is a
> transport header that never enters a canonical byte string.

**One tool, because the substrate has one.** `residency.repository.v1` is
one-shot, so the profile exposes exactly `submit_candidate`. The three
interactive routes exist on the transport **only so they can refuse in the
substrate's words** and relay `KERNEL_OPERATION_UNSUPPORTED` (501). A 404 would
be a claim about this server; the truth is a claim about the substrate.

**The trust boundary is an exact key set.** `RemoteSubmissionV1`
(`traaviis.remote-submission.v1`) has exactly the fields
`{submission_version, finding, patch}` — validated as *the* key set, not against
a denylist. A denylist answers "is this one of the twelve things we thought of";
an exact key set answers "is this the document". Named diagnostics for the
forbidden fields (`reward`, `trace`, `episode_id`, `exit_code`,
`execution_facts`, `run_result`, …) still exist, because "unknown field
`reward`" and "unknown field `rewrad`" deserve different help. Above all a
`runner.RunResult` is refused: it is the *server's* record of what it observed,
and accepting one from the wire would let a client narrate its own execution
into a receipt. The adapter constructs the `RunResult` itself.

**Nothing was executed, and the receipt says so.** The declared runner profile
is `traaviis.ors-submission.v1` — filesystem `not_applicable`, network
`not_applicable`, termination `not_executed`, exit code `null`. The sandbox
posture is not *weaker* here; it is an inapplicable question, and
`not_applicable` is the only honest answer. Claiming `exited` + `0` would assert
that a program ran and succeeded.

**An ORS episode and a local episode over the same candidate honestly differ,
and must not be forced to agree.** The submission trace carries its own version
(`traaviis.submission-trace.v1`), so `trace-…` differs *by construction* rather
than by accident, and the runner profile differs, so `episode-…` differs too.
Both are true statements about two different things that happened. Forcing a
collision would require pretending a submission was a process.

**`finished: true` is a durability claim and is the last thing said.** It is
returned only after the episode has been staged, fully re-verified by replay,
fsynced and atomically published by `write_episode_bundle`. `--output` is
therefore **mandatory** and proven writable *before the socket binds*: finding
it unwritable on the first submission would mean running a candidate's verifiers
and then having nowhere to put the proof. A publish failure is reported as a
failure, never as a finish.

**Admission precedes binding, entirely.** `open_adapter` reopens the package
through §5a (every `task-`/`rew-`/`snap-`/`env-…` re-derived from the bytes on
disk), resolves the split, binds the subject tree, builds one verifier registry
and one kernel, and verifies the output root — and only then may a caller bind
and listen. There is no arrangement of failures that produces a listening server
over a package that did not admit. The served catalog is **restricted to the
split at the kernel**, so an unlisted task is `KERNEL_TASK_UNKNOWN` from the
object that owns task identity, not from a check a later endpoint might forget.

**Concurrency is the §4a lifecycle, exercised.** Two submissions to one session
race into `finalize`; exactly one takes the claim and the loser is refused by
name. Two submissions to *different* sessions proceed in parallel, because no
lock is held across the scoring work at either layer. Publication is
content-addressed and therefore idempotent: two writers that produce the same
episode do not fight, the loser of the rename verifies the winner's tree and
reuses it.

**Idempotency is a transport concern.** The key is the `Idempotency-Key` header:
a repeat replays the stored answer instead of rescoring, and a *different* key
after a finish is refused by name. In the payload it would have been a
client-supplied field that changes server behaviour — the exact category the
exact key set exists to keep empty.

**The default bind is loopback**, and leaving it requires `--allow-remote` — a
flag rather than an inference from the address, so that exposing a server which
holds a candidate's patches and runs verifier commands is something a human
typed.

## 5. D3 + D5 — `env-…` vs `bundle-…`, and the serve process model

> **Status.** `env-…`, `bundle-…`, and `pack` are **BUILT** (`trvs init`,
> `trvs pack`, `trvs verify-bundle`, `trvs archive-bundle`; §5b freezes the
> `bundle-…` rung and §5c the portable-subject-mode and archive-publication
> laws). `serve` remains **DEFERRED** and is specified here for
> continuity only. An **environment** is a *substrate profile + a closed
> subject/task/reward set*, generalized over substrates:
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

- `env-…` moves iff the manifest (subject / tasks / rewards / splits / profiles)
  moves. Presentation-only edits move `bundle-…` only.
- `pack` is **fail-closed** and **substrate-generic**: it re-opens and
  re-verifies the emitted bundle through the substrate admission interface below
  — the subject re-derives to its declared identity and every task / reward /
  subject reference resolves inside the closure — **before** reporting success.

### 5a. Substrate admission interface

`init`, `pack`, and import are written against a **profile-implemented**
interface, not against `world` verbs:

```text
validate_subject             the sealed subject is well-formed for the profile
verify_closure               every task / reward / verifier reference resolves
recompute_subject_identity   re-derive the subject id from its bytes
reopen_package               re-open the emitted artifact and re-verify all laws
```

Each substrate implements it differently:

```text
trvm.world.v1        re-lower source → sem-…; verify scenarios + Forge bundle closure
residency.repository.v1
                     recompute snapshot → snap-…; verify task / reward /
                     verifier / run-policy closure
```

### 5b. `bundle-…` — the distributed package (FROZEN)

`bundle-…` is the content address of the **complete canonical
environment-distribution tree** emitted by `trvs pack`: the environment closure
plus every shipped presentation, documentation and screenshot member, each
identified by **normalized relative path, bytes and canonical mode**.

It is deliberately **not** the hash of an archive's bytes, not the hash of a
batch output directory, not the hash of `batch.json`, and not the hash of a set
of episode results. A package serialized as ZIP and as tar must keep **one**
identity, so compression, member ordering and archive timestamps are outside it.

**`BundleManifestV1`** ships at the package root as **`TRAAVIIS_BUNDLE.json`** —
a name kept deliberately distinct from the operational per-episode `bundle.json`
written by `eval-one`:

```json
{
  "bundle_version": "traaviis.bundle.v1",
  "env_id": "env-<64hex>",
  "members": [{"path": "environment.json", "sha256": "<64hex>", "mode": "0644"}],
  "bundle_id": "bundle-<64hex>"
}
```

`bundle_id = "bundle-" + sha256(canonical(manifest without bundle_id))`. Unlike
every other rung nothing else is projected out, because **the manifest is the
package**: an added field is a changed package.

**Frozen rules.**

- `members` is sorted by normalized relative POSIX path.
- Path, bytes and **canonical mode** are identity-bearing.
- **Canonical mode** is `0644` or `0755` — only the executable bit survives
  distribution, so group/other/umask noise is not a package change. (`pack`
  still writes each member at its *exact* source mode, because the Residency
  snapshot seals raw `file_modes` into `snap-…`.)
- The manifest **excludes itself** from `members`, so no member's hash would
  have to contain its own hash; `bundle_id` is excluded from its own hash.
- Directories are implicit. Symlinks are **refused** in v1. Duplicate, absolute
  and traversing paths are refused.
- **Closure is checked in both directions**: a missing manifested member and an
  extra unmanifested file are both failures.

**Mutation laws.**

| change                                                     | `env-…` | `bundle-…` |
| ---------------------------------------------------------- | ------- | ---------- |
| subject / task / reward / split / profile                   | moves   | moves      |
| name / description / README / screenshot / doc path / mode  | —       | moves      |
| ZIP compression / timestamps / member order                 | —       | —          |

**The `distribution` block.** Source `env.json` carries a presentation-only
block, excluded from `env-…` by the existing identity allowlist:

```json
{"entrypoint": "README.md", "documentation": ["README.md"],
 "screenshots": [], "assets": []}
```

Reclassifying a document as a screenshot moves `bundle-…` with **no byte
change**, because the block lives inside `environment.json`, whose bytes are
themselves a member hash.

**Pack admission order.** Derive `env-…` → verify closure → collect generated
and presentation members → derive `BundleManifestV1` and `bundle-…` → write the
entire tree into a **temporary sibling** → reopen the manifest → verify the
exact member set, hashes and modes → reopen and reverify `env-…` and substrate
closure → **atomically publish**. *No success may be reported solely from the
in-memory pre-write computation.*

**Archives are transport.** `trvs archive-bundle` emits a canonical ZIP (fixed
epoch timestamps, canonical modes, sorted entries) and reports the archive's
SHA-256 **and** the `bundle-…` as two different claims under two different
names. The archive checksum answers "did these bytes arrive intact"; the bundle
id answers "is this the same distributed package". `tools/accept_packet.py`
remains an unrelated **source-release** gate; `bundle-…` neither supersedes nor
wraps it.

**Non-membership.** `EvaluationV1`, `ComparisonV1` and `SerialBatchV1` mint no
bundle identity and carry no `bundle_id`. Distribution identity for batch
*output* is deferred and needs a separate ruling.

`EvaluationV1.bundle` is a **legacy episode-member field**. It predates the
`bundle-…` rung, is not identity-bearing, and its frozen meaning is `null` or
exactly one `episode-<id>` directory name. It is *not* renamed in v1 —
`batch.py` and the B-battery read it — but every human-facing surface calls it
**episode evidence**, and a future `EvaluationV2` renames it `episode_member`.

### 5c. Portable subject-mode closure (FROZEN)

`bundle-…` carries the *canonical* mode (`0644` / `0755` — only the executable
bit is portable), while `residency.repository.v1` seals the **raw** four-digit
mode of every included file into `snap-…`. Both are individually correct and
jointly lethal: a subject file at `0664` seals one `snap-…` before transport and
a different one after, so the package verifies as a package and fails to reopen
as an environment. Two laws close it.

**Subject-mode admission.** For `residency.repository.v1`, every file whose mode
enters `SnapshotV1.file_modes` must already be exactly `0644` or `0755`. Any
other mode — `0600`, `0640`, `0664`, `0775`, and every set-ID mode — is refused
with the typed code **`SUBJECT_MODE_NONCANONICAL`**:

```json
{"paths": {"src/tool.py": {"observed": "0664", "required": "0644"}}}
```

The check runs in one shared helper called from **both**
`recompute_subject_identity` and `reopen_package`, *before* `env-…` and
`bundle-…` are reported as packed — so it blocks authoring a nonportable
package, accepting a hand-built one, and accepting a legacy one whose exact-mode
identity cannot survive canonical transport. Files the snapshot *excludes* never
enter `file_modes` and are therefore out of scope by construction. The law is
substrate-specific: `trvm.world.v1` does **not** inherit it, because a `.wrl`
source file's Unix mode does not enter `sem-…`.

**Archive publication.** `write_archive` publishes only what it has proved *in
serialized form*:

```
verify source tree
→ write a temporary archive
→ extract that archive to a temporary directory
→ verify bundle closure from the extracted bytes
→ when not package-only, re-derive env- and subject closure too
→ compare bundle_id and env_id
→ atomically publish
```

Verifying the directory and then serializing it proves the directory, which is
not the artifact anyone receives. A failed round trip raises
`BUNDLE_ARCHIVE_ROUNDTRIP` and leaves **no** archive at the output path.

## 6. D6 — `init` templates + `pack` admission

**Ruling.**

- **`init`** is **pure environment scaffolding** — *not necessarily world
  scaffolding*. It seeds a subject appropriate to the chosen substrate plus a
  manifest skeleton, and invents no identity. Templates name a substrate:
  `trvs init --template golden-spinner` seeds a TRVM world;
  `trvs init --template evidence-residency` seeds a repository snapshot
  definition. Different templates scaffold genuinely different subjects.
- **`pack`** must **verify closure + recompute subject identity before writing**
  through the §5a admission interface (`validate_subject`, `verify_closure`,
  `recompute_subject_identity`), then `reopen_package` and re-verify the emitted
  artifact. A bundle that fails any law is rejected loudly, never written
  half-formed. For the TRVM profile this reduces to the existing
  `verify_bundle_closure` + Forge re-lower.

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

- `trvs init` — substrate-aware scaffolding against the `env-…` schema (§5, §6).
  **BUILT.**
- `trvs pack` — build + `verify_closure` + `recompute_subject_identity` +
  `reopen_package` through the §5a admission interface, then derive `bundle-…`
  and publish atomically (§5b). **BUILT.**
- `trvs verify-bundle` / `trvs archive-bundle` — re-verify a package tree or
  archive against §5b; emit a canonical archive plus its transport checksum.
  **BUILT.**
- `trvs eval` — run an agent over a split, emit an `episode-…` per task, score.
  **BUILT.**
- `EpisodeKernelV1` — the substrate-neutral episode kernel, extracted and frozen
  *before* any transport (§4a), with `finalize` **linearizable** so a concurrent
  transport cannot double-execute one session. **BUILT** (no CLI verb, by
  ruling).
- `trvs serve --ors` — the Residency Submission ORS Profile v1 over the Episode
  Kernel (§4, §4a, §4b). A translation layer only: it exposes `start` +
  `finalize` and one tool, and relays `KERNEL_OPERATION_UNSUPPORTED` for the
  rest. **BUILT.**
- `trvs serve --mcp` — the same kernel behind the MCP wire vocabulary
  (tools / resources / prompts). **DEFERRED.**

Every construct is implemented **with mutation-law tests first** (the laws in
§2–§6), exactly as the WRL identity spine was built.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
