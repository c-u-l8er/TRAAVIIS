# RFC — Evidence Residency (`residency.repository.v1`)

**Status.** Ruled (user, 2026-07-23). This RFC freezes the first non-TRVM
TRAAVIIS environment *before any code is written*. It scopes exactly one task,
one one-shot agent interface, and the snapshot / output / verifier / reward /
replayability contracts. Nothing here requires WallRiderLang lowering or TRVM
interaction-calculus reduction. Code is held until these contracts are frozen.

**Thesis.** TRAAVIIS is an **evidence-grade environment for evaluating agents**.
Its value is not "deterministic worlds" but: *an agent's work should leave
inspectable evidence that survives replay, verification, disagreement, and human
review.* Evidence Residency is the first environment that makes that concrete for
a real repository, and it is deliberately **not** a TRVM world.

---

## 1. Substrate ruling — TRAAVIIS is substrate-neutral

TRAAVIIS evaluates over multiple substrates. TRVM is one — uniquely strong, but
no longer the definition of everything TRAAVIIS can evaluate.

```text
TRAAVIIS
substrate-neutral evaluation and evidence layer
        │
        ├── TRVM substrate            (trvm.world.v1)
        │     sem · scen · film
        │     reference · native · oracle
        │
        └── Residency substrate       (residency.repository.v1)
              snapshot · finding · patch · trace
              citations · patch · tests · identity
```

`residency.repository.v1` is an **external evidence environment orchestrated by
TRAAVIIS**. TRAAVIIS prepares a frozen subject, runs an agent command in a
controlled workspace, collects evidence, and checks it with external
deterministic verifiers. It does not fold anything through `ic_ref`/`ic32`.

---

## 2. The common ladder (shared) vs substrate-specific artifacts

Four constructs are **shared** across every substrate:

| construct           | question it answers                                        |
| ------------------- | ---------------------------------------------------------- |
| `TaskSpecV1`        | what must be done?                                         |
| `RewardSpecV1`      | how is admissible evidence scored?                         |
| `SubstrateProfileV1`| how is the subject prepared and evidence collected?        |
| `EpisodeReceiptV1`  | what happened, what was produced, what passed, what score? |

Substrate-specific evidence lives **below** the shared ladder:

```text
TRVM       sem · scen · film
Residency  snap · trace · finding · patch
```

### 2a. Correction — `film-…` is TRVM-only; the neutral trace is `trace-…`

A **`film-…` is a TRVM deterministic-execution artifact** and stays that way. It
does **not** generalize to arbitrary substrates. The substrate-neutral
observable record is a **`trace-…`**. The **shared** object is the
**`episode-…`** receipt, which references whichever substrate-specific evidence
exists plus a `substrate_profile`. We do not pretend the underlying execution
mechanisms are identical.

---

## 3. Three meanings of "replay" (freeze this distinction)

| level                   | meaning                                                                                     | who supports it        |
| ----------------------- | ------------------------------------------------------------------------------------------- | ---------------------- |
| **exact replay**        | re-running canonical inputs reproduces the same state trajectory and film                   | TRVM                   |
| **action replay**       | recorded tool calls are reapplied against the same frozen snapshot                          | a controlled Residency runner, *later* |
| **verification replay** | the submitted finding, patch and receipts are re-checked against the frozen snapshot        | **Residency v1**       |

**Residency v1 promises only verification replay.** We do **not** promise
deterministic replay of an arbitrary external coding-agent process — shell tools,
package registries, clocks, and OS behaviour make that a much larger claim.
Residency v1 honestly promises:

> The evidence and outcome can be independently reverified.

`EpisodeReceiptV1.replayability` records which level applies (`exact` for TRVM,
`verification` for Residency v1).

---

## 4. Frozen subject — `SnapshotV1` → `snap-…`

`snap-…` seals **only the subject** — the repository the agent sees — **not the
evaluation procedure**. The same frozen repository must remain the same `snap-…`
even when evaluated under a different test plan.

```json
{ "snapshot_version": "residency.snapshot.v1",
  "snapshot_id": "snap-…",
  "files": { "<relpath>": "<content-hash>", … },
  "exclusions": [ "<glob>", … ],
  "file_modes": { "<relpath>": "0755", … },
  "base_revision": "<vcs-rev or null>",
  "visible_config": { … } }
```

Seal: repository bytes, normalized relative paths, file modes, exclusions, base
revision if available, task-visible repository config. **Do not seal** timestamps
or absolute machine paths — they would make the snapshot machine-specific without
changing its meaning. `snap-…` is a content hash of the canonical sealed bytes.

**Ownership split (erratum).** The evaluation procedure lives in `TaskSpecV1`,
not the snapshot:

- **`SnapshotV1`** seals — repository bytes, relative paths, file modes,
  exclusions, base revision, task-visible repository config.
- **`TaskSpecV1`** seals — declared test commands, required toolchain
  constraints, the verifier plan, and the agent execution policy (§7).
- **`EpisodeReceiptV1`** records — *actual* toolchain versions, *actual* verifier
  versions, command exit codes, and platform facts relevant to verification.

This yields the right mutation behaviour:

```text
change repository bytes   → snap / task / episode may move
change test command       → task / episode move ; snap unchanged
run on a different actual toolchain → episode moves ; task and snap stable
```

---

## 5. Agent outputs — `FindingV1`, `PatchV1`, `TraceV1`

```text
FindingV1 → finding-…    structured claims + citations (not only prose)
PatchV1   → patch-…      a unified diff that applies to a clean snapshot copy
TraceV1   → trace-…      substrate-neutral record of observable process events
```

`FindingV1` must carry **structured** claims and citations, each citation
resolving to exact supporting material inside the snapshot. Spans are
**1-based inclusive line ranges** expressed as an explicit object (never an
ambiguous two-element array), and the `quote` must match the normalized snapshot
bytes exactly:

```json
{ "finding_version": "residency.finding.v1",
  "finding_id": "finding-…",
  "claims": [ { "statement": "…",
                "citations": [ { "path": "spec/WRL_CORE_0.1.md",
                                 "start_line": 120, "end_line": 138,
                                 "quote": "…" } ] } ] }
```

`TraceV1` records observable events — enough to review and to drive *action
replay* later, without claiming determinism now.

### 5a. Canonical evidence rules (freeze before generating any IDs)

**Snapshot paths.** UTF-8 relative POSIX paths; no absolute paths; no `..`; `/`
separator on every platform; explicit symlink policy (v1: symlinks excluded);
explicit line-ending policy (v1: normalize to LF for text, byte-exact for
declared-binary).

**Citations.** 1-based inclusive line ranges as `{start_line, end_line}`; the
quoted text matches the normalized snapshot bytes exactly.

**Patches.** Unified diff; relative paths only; no timestamps; fixed `a/` and
`b/` prefixes; LF line endings; **no binary patches in v1**; canonical file
ordering (sorted by POSIX path).

**Traces.** Exclude volatile timestamps and host paths from `trace-…` identity.
The canonical trace records deterministic events only:

```text
command argv · relative cwd · declared environment keys · exit code
stdout digest · stderr digest · files-created digest · files-modified digest
result-file digest
```

Human-readable timed logs may exist beside the canonical trace **without**
entering its identity.

---

## 6. Verifier applicability — four states

Every verifier returns exactly one of:

```text
pass · fail · not_applicable · error
```

`error` **must remain distinct** from `fail`: an unavailable test runner is not
evidence that the candidate patch is wrong. `not_applicable` is coverage honesty
(e.g. `native`/`oracle` on a Residency episode). The receipt's verification map
is **total** — every declared verifier reports one of the four.

Residency v1 verifier set:

| verifier   | checks                                                             |
| ---------- | ----------------------------------------------------------------- |
| `citations`| every finding citation resolves and the quote matches source      |
| `patch`    | the patch applies cleanly to a fresh copy of `snap-…`             |
| `tests`    | the declared `test_commands` pass on the patched copy             |
| `identity` | semantic identity moved only within allowed domains               |
| `native`   | `not_applicable` (TRVM-only)                                       |
| `oracle`   | `not_applicable` (TRVM-only)                                       |

The `identity` verifier is where TRVM's guarantees re-enter: if the task touches
a WRL world, the frozen `sem-…` domains that must not move are checked with the
existing Forge re-lower — a Residency episode can *depend on* a TRVM identity
check without *being* a TRVM fold.

### 6a. Verifier state → reward behavior (frozen)

The four verifier states map to reward behavior by a fixed table. **A test
runner failure is not evidence that the agent's work was incorrect** — it is
substrate unavailability, and it must never be scored as a `fail`.

| State                                     | Reward behavior                                            |
| ----------------------------------------- | --------------------------------------------------------- |
| `pass`                                    | signal receives its positive value                        |
| `fail`                                    | signal receives zero; applicable floors/caps run          |
| `not_applicable`                          | allowed only when the task declares the signal non-required |
| `error`                                   | episode `status` becomes `error`; reward is `null`, not `0` |
| required verifier returns `not_applicable`| invalid task configuration (`status: invalid`)            |
| snapshot tampering                        | invalid episode (`status: invalid`); reward `0`           |

The distinction between `null` (an `error` episode: scoring could not be
computed) and `0` (a real `fail` or a tamper) is load-bearing. Downstream
aggregation must drop `null`-reward episodes, never average them in as zeros.

---

## 7. Reward — deterministic, decomposable, no LLM prose judging

`RewardSpecV1` for the first task:

```text
citation validity      0.25
patch applicability    0.20
tests                  0.30
identity discipline    0.15
finding completeness   0.10
```

**`finding completeness` means required structured fields + evidence coverage**,
never prose quality judged by another model. No signal in Residency v1 calls an
LLM; every signal is a pure function over sealed evidence.

**Hard floors (caps, applied after the weighted sum):**

```text
patch does not apply        → reward ≤ 0.25
citations do not resolve    → reward ≤ 0.25
required tests regress      → reward ≤ 0.40
tampered snapshot           → reward = 0 and episode marked invalid
```

`rew-…` is the content hash of the canonical `RewardSpecV1` (weights + floors +
verifier binding).

---

## 8. First task (one, narrow, bounded)

```text
task: residency/wrl-spec-impl-inconsistency-001

Identify one real inconsistency between a frozen WallRiderLang specification and
its implementation. Cite the exact conflicting evidence, propose the smallest
patch, run the declared acceptance checks, and preserve identities that should
not move.
```

Bounded corpus, explicit success contract. The agent is **not** asked to search
for any possible improvement — it is given a fixed snapshot and a single,
checkable objective.

---

## 9. Mutation laws (freeze; test-first before code)

```text
change repository input
  → snap / task / trace / finding / patch / episode may move

change task instructions
  → task / episode move
  → snap unchanged

change reward weights
  → rew / episode move
  → snap / trace / finding / patch unchanged

change agent output
  → finding / patch / trace / episode move
  → task / snap unchanged

rerun verifiers on identical frozen artifacts
  → episode identity remains stable

change verifier implementation version
  → episode moves
  → underlying evidence artifacts (snap / trace / finding / patch) unchanged
```

The last law is load-bearing: **verifier versions are recorded in the receipt**,
so a scoring-logic change is auditable and moves the `episode-…` without
rewriting what the agent actually produced.

---

## 10. `trvs eval-one` — the one-shot interface (first implementation)

```bash
trvs eval-one residency/wrl-spec-impl-inconsistency-001.json \
  --agent-command "./stub-agent"
```

The agent command receives a frozen workspace and emits one structured final
result:

```json
{ "format": "traaviis.agent-result.v1",
  "finding": { "summary": "…", "citations": [ … ] },
  "patch_path": "candidate.patch" }
```

TRAAVIIS then, independently and deterministically:

1. snapshots the starting repository → `snap-…`;
2. runs the command in a controlled workspace;
3. captures observable process events → `trace-…`;
4. reads the proposed finding and patch → `finding-…`, `patch-…`;
5. resolves citations against `snap-…`;
6. applies the patch to a clean copy of `snap-…`;
7. runs the declared `test_commands`;
8. measures identity movement;
9. calculates the reward (weights + floors);
10. emits `episode-…`.

A richer interactive N-step action protocol (and the ORS adapter over the Episode
Kernel) can follow **after** this one-shot path works and is boring.

### 10a. `AgentRunPolicyV1` — the frozen one-shot execution policy

The task carries an `agent_run_policy` sub-document. It is part of the
`TaskSpecV1` identity: changing how the agent is allowed to run changes `task-…`.
The runner enforces it; the receipt records what actually happened.

```json
{ "policy_version": "traaviis.agent-run-policy.v1",
  "command_mode": "argv",
  "shell": false,
  "network": "disabled",
  "timeout_seconds": 900,
  "max_output_bytes": 4194304,
  "environment_allowlist": ["PATH", "HOME", "LANG"],
  "writable_paths": ["."],
  "result_path": "result.json",
  "patch_path": "candidate.patch" }
```

Rules:

- **`command_mode: argv`, `shell: false`** — the agent command is an argv vector
  executed without a shell. No shell interpolation is part of the contract.
- **`network: disabled`** by default — a task that needs the network must declare
  it explicitly, and that declaration moves `task-…`.
- **`timeout_seconds` / `max_output_bytes`** are hard bounds. Exceeding either
  terminates the run; the episode records the termination reason and the
  affected verifier reports `error` (substrate unavailability), not `fail`.
- **`environment_allowlist`** — only listed keys are passed through; everything
  else is stripped so the environment is not a hidden input to identity.
- **`writable_paths`** scopes filesystem mutation; writes outside the set are a
  policy violation that invalidates the episode.
- **`result_path` / `patch_path`** are where the runner reads the structured
  result and the candidate patch after the process exits. Absence of a required
  output is a `fail` for the corresponding verifier, never an `error`.

The distinction is deliberate: a **policy or substrate failure** (timeout,
output cap, missing runner) yields `error`; a **missing or malformed agent
output** yields `fail`.

---

## 11. Example receipts

**TRVM episode** (Golden Spinner, unchanged):

```json
{ "episode_version": "traaviis.episode.v1",
  "substrate_profile": "trvm.world.v1",
  "task_id": "task-…",
  "subject": { "world": "sem-…", "scenario": "scen-…" },
  "trace": "film-…",
  "verification": { "reference": "pass", "native": "pass", "oracle": "pass" },
  "verifier_versions": { "reference": "1", "native": "1", "oracle": "1" },
  "reward": 1,
  "status": "ok",
  "validity": "valid",
  "replayability": "exact",
  "execution_facts": { "reducer": "ic32", "epochs": 9 } }
```

**Evidence Residency episode:**

```json
{ "episode_version": "traaviis.episode.v1",
  "substrate_profile": "residency.repository.v1",
  "task_id": "task-…",
  "subject": { "snapshot": "snap-…" },
  "trace": "trace-…",
  "outputs": { "finding": "finding-…", "patch": "patch-…" },
  "verification": { "citations": "pass", "patch": "pass", "tests": "pass",
                    "identity": "pass",
                    "native": "not_applicable", "oracle": "not_applicable" },
  "verifier_versions": { "citations": "1", "patch": "1", "tests": "1", "identity": "1" },
  "reward": 1.0,
  "status": "ok",
  "validity": "valid",
  "replayability": "verification",
  "execution_facts": { "exit_code": 0, "platform": "linux-x86_64",
                       "timed_out": false, "output_truncated": false } }
```

`status` is `ok | error | invalid`; `validity` is `valid | invalid`. An `error`
episode carries `reward: null`. `execution_facts` records the actual run
(exit codes, platform, truncation) and is **excluded** from the `episode_id`
hash together with wall-clock timestamps, absolute paths, and transient PIDs.

Same ladder, honest about different execution mechanisms.

---

## 12. Held until this RFC is accepted

Not built now: the interactive N-step environment protocol, ORS/MCP adapters,
batch `eval` / `compare`, action replay, a second Residency task, process
rewards, and any new identity category beyond `snap-…` / `trace-…` / `finding-…`
/ `patch-…` / `episode-…`. Spinner Bench v0.7-5 proceeds independently and is not
a blocker for this RFC.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
