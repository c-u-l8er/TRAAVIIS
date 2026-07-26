# TRAAVIIS

**Evidence-grade environments for evaluating agents.**
Write the wall. Run the world. Keep the proof.

TRAAVIIS provides **evidence-grade environments for evaluating agents** and
proves what happened inside them. TRVM worlds are its strongest deterministic
substrate; **Evidence Residency** is its first repository-evidence substrate. In
the TRVM world substrate you write a world in **WallRiderLang**; `trvs` lowers it
to a content-addressed `SemanticArtifactID`, folds each episode into a replayable
**film**, and verifies that film with every applicable check: a pure reference
reducer, a compiled native reducer, and an independent oracle where its domain
applies. Coverage is made explicit — a verifier that cannot apply is reported
`not_applicable`, never as pass or fail. Same world, same scenario, same
trajectory — same hash, every time.

`trvs` carries **no world semantics of its own**. It is a thin, honest terminal
over the existing Forge/TRVM engine (the `wrl_*.py` identity/lowering spine plus
the `ic_ref` and `ic32` reducers). **TRAAVIIS does not embed, select, or route a
model.** The shipped world commands call none; evaluation (`eval-one`) runs an
agent command *supplied by the user*, which may itself use a model.

> **The differentiation:** not merely "deterministic environments," but
> *content-addressed* ones. Two researchers can answer four questions and never
> argue about the answers — did we run the same world, the same scenario, the
> same trajectory, and did the verifiers agree?

## Install

```sh
pipx install traaviis        # or: pip install traaviis
trvs doctor                  # check the engine + verifiers are on-path
```

**No third-party Python dependencies.** Requires Python ≥ 3.9, a compatible
Forge/TRVM engine reachable at runtime (see `traaviis.engine`), and — for native
verification — the `ic32` executable (otherwise `verify` degrades to the
reference reducer).

## The command set

Eight commands ship today and fold real worlds over the engine. The
**environment surface** turns a subject into something an agent can be evaluated
against; its **beachhead — `trvs eval-one`** — now ships: a one-shot,
trusted-local evaluation of a single frozen subject that admits the bundle,
binds the subject byte-exactly, runs a user-supplied agent, and returns a
content-addressed `episode-…` receipt. Batch evaluation, packaging and serving
come after.

| command         | what it does                                             | status         |
| --------------- | -------------------------------------------------------- | -------------- |
| `trvs doctor`   | engine location, versions, verifier availability         | shipped        |
| `trvs id`       | the world's `SemanticArtifactID` — pure identity         | shipped        |
| `trvs inspect`  | actors, edges, resolved config, diagnostics              | shipped        |
| `trvs run`      | lower + deterministically fold; per-epoch film           | shipped        |
| `trvs verify`   | reference / native / oracle agreement (strict)           | shipped        |
| `trvs replay`   | re-fold a film and assert it reproduces (`--expect`)     | shipped        |
| `trvs diff`     | compare two worlds' identity + per-epoch films           | shipped        |
| `trvs eval-one` | evaluate one agent run over one frozen subject           | shipped        |
| `trvs init`     | scaffold an environment for a substrate template         | shipped        |
| `trvs pack`     | close a scaffold into a verified, reopened package       | shipped        |
| `trvs eval`     | run an agent over a split, score every episode           | shipped        |
| `trvs verify-episode` | re-verify a saved episode bundle, no agent run    | shipped        |
| `trvs compare`  | rank two closed episodes answering one task              | shipped        |
| `trvs batch`    | run several candidates over one split, compare per task  | shipped        |
| `trvs verify-bundle`  | re-verify a package tree or archive against `bundle-…` | shipped  |
| `trvs archive-bundle` | emit a canonical archive + its transport checksum  | shipped        |
| `trvs serve --ors` | serve a packed environment as a submission endpoint   | shipped        |
| `trvs serve --mcp` | the same kernel behind the MCP wire vocabulary       | later          |

Every shipped command takes `--json` for CI / agent consumption.

## Use (shipped today)

```sh
trvs id      worlds/alley.wrl                 # sem-8ae91fe9…fe4a
trvs inspect worlds/alley.wrl                 # actors, edges, config, diagnostics
trvs run     worlds/alley.wrl                 # per-epoch film strip
trvs verify  worlds/alley.wrl                 # reference · native · oracle → 3/3
trvs diff    worlds/alley.wrl worlds/alley_n4.wrl

# replay pins a *film* — the trajectory, not the world's identity:
film="$(trvs run worlds/alley.wrl --json | python -c 'import json,sys; print(json.load(sys.stdin)["epochs"][-1]["film"])')"
trvs replay  worlds/alley.wrl --film "$film"   # asserts the fold reproduces that film
```

`replay` also accepts `--expect sem-…` to assert the source still lowers to a
pinned **semantic identity** — a different question (same *meaning*) from `--film`
(same *trajectory*). Keep the two domains distinct.

Exit-code contract for `verify` / `replay` / `diff`: **0** agree/reproduced,
**1** ran and disagreed/drifted, **2** a verifier was unavailable or the source
failed to lower. That makes any of them a fail-closed gate in CI or an RL loop.

### Evaluate one agent run (`eval-one`, shipped)

`trvs eval-one` takes an **eval-bundle** directory (a `bundle.json` manifest that
names the task, reward, snapshot and frozen `subject/`) and a user-supplied agent
command. It admits the bundle before it runs anything — recomputing every
declared id, cross-binding the task to *these* reward + snapshot, binding the
working subject byte-exactly to the sealed snapshot (modes and declared binaries
included), and rejecting any run policy the trusted-local runner cannot honor —
then folds one episode into a content-addressed `episode-…` receipt.

```sh
# the in-repo residency demo, scored against the deterministic stub agent:
trvs eval-one examples/eval-one/residency-demo \
    --agent python3 "$PWD/test/fixtures/stub_agent.py" --platform linux-x86_64

# dashed agent flags pass through unchanged after a standalone `--`:
trvs eval-one examples/eval-one/residency-demo \
    --platform linux-x86_64 -- my-agent --model foo --temperature 0
```

Exit codes mirror the receipt's status: **0** a valid episode, **1** an invalid
one (policy violation / invalid config), **2** admission rejected the bundle
before execution or a substrate error prevented scoring.

### Author an environment (`init` + `pack`, shipped)

`init` scaffolds and **derives no identity**; `pack` is where identity is earned.
Splitting them is the point: a scaffold that shipped a pre-baked `snap-…` would
be asserting a content hash nobody computed, so `init` emits references
(`reward_spec`, `snapshot_def`) and `pack` replaces them with recomputed ids.

```sh
trvs init --list                                  # templates and their substrates
trvs init --template evidence-residency my-env    # seeds a subject + skeletons
trvs pack my-env my-env-pkg                       # closes it into a package
trvs eval-one my-env-pkg --agent … --platform linux-x86_64
```

`pack` follows the §6 order literally: validate the subject → recompute its
identity **from the bytes** → bind and close reward/task/manifest → verify
closure → *then* write atomically → reopen the written package and re-derive
every id from what actually landed on disk. Any broken law is exit 2 with a
typed code, and nothing is left behind. A single-task residency package is also
a runnable eval-bundle, so authoring closes the loop back into `eval-one`.

| template             | substrate                 | subject seeded              | asks                          |
| -------------------- | ------------------------- | --------------------------- | ----------------------------- |
| `golden-spinner`     | `trvm.world.v1`           | a WallRiderLang world       | is the world's identity held? |
| `evidence-residency` | `residency.repository.v1` | a repository snapshot       | is the patch admissible?      |
| `residency-repair`   | `residency.repository.v1` | a repository with a defect  | was the bug fixed?            |

The last two share a substrate and differ in what they can catch.
`evidence-residency` seeds `return 1` and accepts either `return 1` or `return 2`,
so its acceptance test cannot fail on the subject it ships with — a conformance
fixture. `residency-repair` seeds a subject that disagrees with its own spec and
declares the target test **red on the baseline** (`baseline: [1]` → `patched:
[0]`), plus a repository-health control that must stay green in both phases. An
admissible, correctly-cited patch that does not actually fix the defect is capped
by the tests signal, and so is one that satisfies the target by wrecking the
repository around it.

Templates are written against the **substrate admission interface** (`validate_subject`,
`verify_closure`, `recompute_subject_identity`, `reopen_package`), never against
world verbs — a third substrate is a new profile, not a new branch in the packer.

### Ship a package (`bundle-…`, shipped)

`env-…` answers *"is this the same environment?"*. `bundle-…` answers *"is this
the same **distributed package**?"* — the environment closure **plus** every
shipped README, doc and screenshot, each pinned by normalized path, bytes and
canonical mode:

```sh
trvs pack my-env my-env-pkg              # prints env, bundle, and each shipped member
trvs verify-bundle my-env-pkg            # re-derive from disk; closure both directions
trvs archive-bundle my-env-pkg out.zip   # canonical archive + transport checksum
trvs verify-bundle out.zip               # same bundle-… out of the archive
```

Which means the two ids move independently, by construction:

| change                                                    | `env-…` | `bundle-…` |
| --------------------------------------------------------- | ------- | ---------- |
| subject / task / reward / split / profile                  | moves   | moves      |
| name / description / README / screenshot / doc path / mode | —       | moves      |
| ZIP compression / timestamps / member order                | —       | —          |

That bottom row is the reason `bundle-…` addresses a **logical tree** and not
archive bytes: the same package shipped as ZIP and as tar has to keep one
identity. The archive's own SHA-256 is still reported — it answers a *different*
question ("did these bytes arrive intact") and `archive-bundle` prints both
under different names so they are never confused.

Presentation is genuinely inert where it matters: re-describe a release, rewrite
its README, add a screenshot, and every `episode-…` already recorded against it
stays exactly as valid, because none of that ever entered `env-…`. A package
declares what it ships in a presentation-only `distribution` block
(`entrypoint` · `documentation` · `screenshots` · `assets`), so even
*reclassifying* a doc as a screenshot moves `bundle-…` — with no byte change,
since the block lives inside `environment.json`, whose bytes are a member hash.

`pack` publishes atomically: the whole tree is written to a temporary sibling,
reopened, re-verified member-by-member **and** re-verified through the substrate
admission interface, and only then renamed into place. No success is ever
reported from the in-memory computation alone, and a failed pack leaves no
destination behind.

`archive-bundle` publishes on the same terms, one level down. It writes the ZIP
to a temporary path, **extracts it**, re-derives `bundle-…` — and `env-…` and the
subject closure, unless you passed `--package-only` — from the extracted bytes,
and only then renames it into place. Verifying the directory and then serializing
it would prove the directory, which is not the artifact anyone receives.

#### Subject file modes must already be portable

A distributed package carries only the executable bit (`0644` / `0755`), while a
Residency snapshot seals the **exact** four-digit mode into `snap-…`. Both are
right, and together they used to be lethal: a subject file at `0664` sealed one
`snap-…` before transport and a different one after, so the package verified as a
package and failed to reopen as an environment.

So `residency.repository.v1` now refuses a subject file whose mode is not already
`0644` or `0755`, at both the authoring seam and the reopening seam, before
either id is reported:

```
trvs: [SUBJECT_MODE_NONCANONICAL] authoring this subject: 1 subject file(s)
      carry a file mode that cannot survive canonical distribution …
      paths: {'src/tool.py': {'observed': '0664', 'required': '0644'}}
```

Files the snapshot *excludes* never enter the seal, so their modes are out of
scope. Presentation members — README, docs, screenshots — are outside `snap-…`
entirely, so a `0664` README is fine and normalizes to `0644` in transport
without moving either id. And the rule is substrate-specific: `trvm.world.v1`
does not inherit it, because a `.wrl` file's Unix mode does not enter `sem-…`.
`trvs init` writes every scaffolded file at an explicit `0644`, so a loose umask
cannot produce an environment that its own packer refuses.

### Run a split (`eval`, shipped)

`eval-one` answers *"did this agent solve this task?"*. `eval` answers *"did it
solve this split?"* — one `episode-…` per task, each independently replayable:

```sh
trvs eval my-env-pkg --split test --output episodes/ --agent … --platform linux-x86_64
trvs verify-episode episodes/episode-cfef6da4…      # replays with no agent
```

```
  [1/2] ✓ task-082e11c181f3be21aa82e138  ok  reward 1
  [2/2] ✓ task-0cd4992e274e07fc7da0bddd  ok  reward 1
  ok            2/2
  mean reward   1
  episodes kept 2/2
```

Everything checkable is checked **before a single agent process starts**: the
package is reopened through the admission interface (every `task-`/`rew-`/
`snap-` re-derived from the written bytes), the split resolves, and the subject
tree binds to its snapshot. A tampered package or a drifted subject costs zero
episodes. Once running, a bad task is *recorded* as a bad episode — it never
aborts the split, and a refusal is never reported as a score of zero.

`ok` and `episodes kept` are printed as two lines because they answer two
questions. `ok` is what the *evaluation* found; `episodes kept` is whether the
evidence you asked for is still on disk. A run can be fully `ok` and still have
failed to retain its own proof — a read-only `--output`, a full disk — and a
score with no retained proof is not a result you can take away. So the exit
code reads them in precedence: **0** all ok and all kept · **1**
ran-and-disagreed · **2** could not run, *or* could not keep what was asked for.

An environment is a closed **set** (§3), and a set has exactly one written
order. `pack` therefore stores tasks and rewards sorted by their derived ids and
splits sorted by task id, so reordering an author's source list does not move
`env-…`. That order is re-checked **on reopen**, not merely produced by `pack`:
a hand-built manifest that is internally consistent but unsorted is refused
(`MANIFEST_NONCANONICAL`) rather than admitted as a second identity for the same
environment. `eval` emits **no new artifact id** — the ladder stops at `env-…`,
so its `evaluation.json` is an *index* over episodes that are each already
content-addressed.

One naming note, because two different things nearly read as one. Each entry in
`evaluation.json` carries a field named `bundle`. It is a **legacy
episode-member field** that predates the `bundle-…` rung by several slices, it
carries no package identity, and its frozen meaning is `null` or exactly one
`episode-<id>` directory name. Every human-facing surface calls it *episode
evidence*; nothing in v1 ever writes a `bundle-…` there; and a future
`EvaluationV2` renames it `episode_member`.

### Serve a submission endpoint (`serve --ors`, shipped)

`eval` launches the agent. `serve --ors` is the other direction: the agent lives
somewhere else and *submits* a candidate, and this host scores it.

```sh
trvs serve my-env-pkg --ors --split all --output episodes/ --port 8791
```

```
  environment   env-a38ec4c04532be258a59663588b0b7e678bf7ad31eee8173e09b35f4229740e2
  split         all
  substrate     residency.repository.v1
  profile       traaviis.ors-profile.v1
  runner        traaviis.ors-submission.v1
  listening     http://127.0.0.1:8791/ors/v1
  episodes      /tmp/orsdemo/episodes

  tools
  ✓ submit_candidate

  refused by this substrate
  ✗ observe
  ✗ reset
  ✗ step

  loopback only. ctrl-c to stop.
```

The banner is the profile. `residency.repository.v1` is one-shot, so there is
**one tool**, and the three interactive routes exist only so they can refuse
with the substrate's own `KERNEL_OPERATION_UNSUPPORTED` (HTTP 501) rather than a
404 — a 404 would claim the endpoint is missing, which is a statement about this
server; the truth is a statement about the substrate, and a client deserves to
be told which. Nothing is advertised that cannot be done.

A client opens a session and submits, and gets back the reward and the episode
that proves it:

```sh
curl -sX POST localhost:8791/ors/v1/sessions -d '{"task_id":"task-3b4b2599…"}'
curl -sX POST localhost:8791/ors/v1/sessions/session-2ccd00b1…/call_tool \
     -H 'Idempotency-Key: demo-1' \
     -d '{"tool":"submit_candidate","arguments":{ … }}'
```

```json
{"reward": 1.0, "finished": true,
 "metadata": {"episode_id": "episode-0a258d6e…", "status": "ok",
              "validity": "valid", "evidence_member": "episode-0a258d6e…"}}
```

```sh
trvs verify-episode episodes/episode-0a258d6e…    # replays it, no agent → closed
```

Four properties are what make this more than an HTTP wrapper:

* **The client supplies a finding and a patch, and nothing else.**
  `RemoteSubmissionV1` is validated by **exact key set**, not by a denylist: a
  denylist answers "is this one of the twelve things we thought of", an exact
  key set answers "is this the document". A `reward`, a `trace`, an
  `episode_id`, an `exit_code` — above all a `RunResult`, which is the
  *server's* record of what it observed — are refused by name, so a client
  cannot narrate its own execution into a receipt.
* **`finished: true` is a durability claim, and it is the last thing said.** It
  is returned only after the episode has been staged, fully re-verified by
  replay, fsynced, and atomically published. `--output` is therefore mandatory
  and is proven writable *before the socket binds* — discovering it on the first
  submission would mean running a candidate's verifiers and then having nowhere
  to put the proof. A publish failure is reported as a failure, never as a
  finish.
* **Admission happens entirely before binding.** The package is reopened, every
  id re-derived from the bytes on disk, the split resolved, the subject bound,
  the verifier registry built, and the kernel constructed — *then* the socket is
  bound and the banner printed. There is no arrangement of failures that yields
  a listening server over a package that did not admit, and a served catalog is
  exactly the split (an unlisted task is `KERNEL_TASK_UNKNOWN` at the kernel).
* **An ORS episode and a local episode honestly differ.** The submission trace
  carries its own version and the runner profile says `not_executed` with a null
  exit code, because nothing was executed here. Claiming `exited` + `0` would
  assert that a program ran and succeeded. So `trace-…` and `episode-…` differ
  from the local run of the same candidate — two true statements about two
  different things, rather than one forced collision.

Idempotency is a **transport** header (`Idempotency-Key`), never a submission
field: a retry with the same key replays the stored answer instead of rescoring,
a *different* key after a finish is refused by name, and putting it in the
payload would have made it a client-supplied field that changes server
behaviour — the exact category the exact key set exists to keep empty.

The default bind is loopback and leaving it takes `--allow-remote`. This server
holds a candidate's patches and runs verifier commands; the default for a thing
like that is not "reachable from the network", and it is a flag rather than an
inference from the address so that exposing it is something a human typed.

### Comparing two candidates

`compare` answers *"which of these two did better, and where did they differ?"*
over evidence that already exists. It runs **no agent**: both bundles are opened
and replayed to `closed` first, and a bundle that will not reverify is not
comparable.

```sh
trvs compare episodes-a/episode-cbce4bfb… episodes-b/episode-3505e1ab…
```

```
  task          task-20c891ea538e3f050b1862358039a2546dc5db7afeabb83d68e4cd79a5aacd87
  reward        rew-25c4ce1276a7b70a473548354ae5d14f1c852ba087ffd09bc9b17b550c5c05a5

  episodes
  left  ok       reward 1
        episode-cbce4bfbcecd6f7f1f6e3750beb34c8d10ea978fa091a843018ba330c61783e0
  right ok       reward 0.4
        episode-3505e1ab29f1ce85bef2a91017f66dd84f5eeff0f8fe4273eb05f5e87989b8fe

  relation      left scored higher
  right - left  -0.6
  same episode  no
  same trace    no

  differs in    outputs, verification, verification_evidence
```

The two must share one `task_id`. That is what makes the two numbers mean the
same thing: a `task-…` fixes the task bytes, and through them the frozen subject
and the reward binding, so both episodes were scored by one rubric. Comparing
rewards across two tasks is comparing two different questions, and is refused
(`TASK_MISMATCH`).

Two rules the reward relation does not break:

* **A null reward is `incomparable`, never zero.** An errored or unscored
  episode did not score badly; it did not score. Imputing zero would rank a
  fixture failure below a bad-but-real attempt. The delta is withheld too, so a
  consumer cannot arithmetic its way past a missing score.
* **Equal rewards do not hide a different trace.** There is no secondary
  tie-breaker. Two candidates that scored the same *are* equal under the rubric;
  that they got there differently is reported as a trace relation rather than
  folded into the ranking.

`ComparisonV1` is an ordinary deterministic report. It carries **no id of its
own** and mints nothing — there is no `compare-…` rung, because a comparison is
a *reading* of two sealed episodes, not a new artifact anyone needs to
re-derive. Everything in it is already addressed by the ids it quotes. Exit
codes: **0** comparison produced (whichever side won) · **1** a bundle
reverified as an evidence mismatch · **2** a bundle was unavailable, a verifier
was unavailable, or the two answer different tasks.

### Batching several candidates

`eval` runs **one** candidate over a split. `batch` runs several, serially, over
the same frozen package — and compares them task by task. It is exactly the
composition of the two commands above and adds no third mechanism: every
candidate goes through `eval_split`, every pair through `ComparisonV1`.

```sh
trvs batch pkg all --candidates candidates.json --output batch-out
```

```json
{
  "candidate_set_version": "traaviis.candidate-set.v1",
  "candidates": [
    {"candidate_key": "gutspec", "argv": ["python3", "repair_agent.py", "gutspec"]},
    {"candidate_key": "nofix",   "argv": ["python3", "repair_agent.py", "nofix"]},
    {"candidate_key": "repair",  "argv": ["python3", "repair_agent.py", "ok"]}
  ]
}
```

```
  environment   env-2a28f0add72f232bdb8b2420a649f0fe9fa7dd749967d28e89252807bc43f090
  split         all (2 tasks)
  candidates    gutspec, nofix, repair

  task-20c891ea538e3f05
    ✗ gutspec      ok       reward 0.4
    ✗ nofix        ok       reward 0.4
    ✓ repair       ok       reward 1
      gutspec vs nofix: equal reward
      gutspec vs repair: right scored higher
      nofix vs repair: right scored higher
  …

  episodes      6 (6 scored, 0 unscored)
  comparisons   6
  batch         batch-out/batch.json
```

`--output` is **mandatory**: a comparison replays *persisted* closures, so a
batch that kept nothing would have nothing to compare. The published tree is

```
batch-out/
├── batch.json
├── candidates/<key>/evaluation.json
├── candidates/<key>/episodes/episode-…/
└── comparisons/task-…/<left>--<right>.json
```

and it appears by **one rename** — a reader never sees a matrix with three of
four candidates in it, and an aborted batch leaves no directory at all.

A `candidate_key` is a **local report label, not an agent identity**. Rename
every candidate and rerun: every `episode-`, `task-`, `trace-` and `env-` is
byte-identical, because the mode rides in `argv` and the task bytes never move.
`SerialBatchV1` therefore carries **no id of its own**, and there is no
`batch-…`, `candidate-…`, `agent-…` or `compare-…` rung.

What holds the matrix together:

* **One environment, one subject, one registry.** The package is opened, the
  split resolved and the subject admitted *once*, before anything launches; one
  `VerifierRegistryV1` both runs and replays every episode, so a comparison is
  never judged by verifiers other than the ones that produced the evidence.
* **Serially, in sorted `candidate_key` order.** Two agents running at once
  share a host, and a candidate that lost a race would be scored for it.
* **A lost cell refuses; it does not stop the batch.** A candidate that retains
  no episode for a task yields a typed pair refusal naming both sides — never a
  fabricated relation — and the remaining candidates still run and are still
  compared with each other.

Exit codes: **0** the batch completed and its report was written (whatever the
candidates scored — a loss, a null reward or a valid errored episode belongs in
the report, not in the exit code) · **1** evidence persisted by this run failed
to reverify during comparison · **2** admission, verifier availability, a
malformed candidate set, or a failure to write.

### Verifiers: plan, implementation, history

Three different things are easy to conflate, so they are named separately:

* the **declared plan** is a function of the task alone — which signals the
  task's policies call for. Two callers reading the same task always compute the
  same plan.
* the **available implementations** are a function of the task *and* the
  runtime: a `VerifierRegistryV1` built once per command from the engine that
  command selected. If the Forge engine is unreachable, `identity` is not
  silently dropped and not faked — it is declared, unwired, and reported.
* the **sealed history** is `receipt.verifier_versions` — what actually answered,
  at which version. It enters `episode-…`, so an engine upgrade honestly moves
  the episode id rather than quietly re-scoring the same one.

### Test plans — `traaviis.test-plan.v2`

A V2 command names its interpreter *logically* (`"tool": "python3"` under a
`toolchain_profile`) instead of baking an absolute host path into `argv`, so
`task-…` is host-independent; the resolved binary is an execution fact, not part
of the task. Each command may also declare per-phase expectations:

```json
{"tool": "pytest", "args": ["-q", "test/test_bug.py"],
 "baseline": {"allowed_exit_codes": [1]},
 "patched":  {"allowed_exit_codes": [0]}}
```

Both default to `[0]`, which is exactly the V1 rule, so an undeclared plan keeps
its old meaning. Declaring them is what makes a **repair task** expressible at
all: "this test must fail before the fix and pass after" is the ordinary shape of
a real bug report, and under a hardcoded baseline-must-exit-0 rule it could not
be written down. The asymmetry is deliberate — the baseline judges the *fixture*,
so a baseline that misses its expectation is `error` (the task is inadmissible);
the patched run judges the *candidate*, so a miss there is `fail`.

## The environment surface

A tool list tells an agent what it *can* call; it does not define tasks, splits,
rewards, episode completion, or reset. So TRAAVIIS's internal contract is a
**neutral Episode Kernel** — and public protocols are *adapters* over it, never
runtime law:

```text
EpisodeKernelV1  list_tasks · start · observe · step · reset · finalize · close
        ↓                                           (internal, neutral, SHIPPED)
local runner     trvs eval-one / trvs eval          →  start → run → finalize
ORS adapter      first / primary public surface     →  trvs serve --ors (SHIPPED)
MCP adapter      compatibility (tools/resources/prompts) → trvs serve --mcp
JSONL adapter    local automation / debugging
```

The kernel is **shipped** (`traaviis/kernel.py`, RFC §4a) and deliberately has
**no `trvs` verb of its own**: it was extracted *before* any transport exists,
which is the only order in which the extraction means anything. A kernel written
after a server describes what that server needed; a kernel written first states
what an episode is, and the server has to translate. `trvs eval-one` and
`trvs eval` are already adapters over it — `start → run_agent → finalize` —
producing the same `episode-…` receipts, byte for byte, as before the split.

Four things about it are worth knowing before writing an adapter:

* **A `session_id` is not an identity.** It is an ephemeral in-process handle,
  never a rung of the ladder below, never written into a receipt or a bundle.
* **Unsupported means refused.** Residency v1 is one-shot: it supports
  `list_tasks`, `start`, `finalize`, `close`, and answers `observe` / `step` /
  `reset` with the typed `KERNEL_OPERATION_UNSUPPORTED`. It will not tell an
  agent an action applied when nothing applied. A consequence worth stating up
  front: since a remote client cannot `observe` a Residency session at all,
  `trvs serve --ors` over Residency exposes exactly `start` + `finalize` and
  one tool — see [Serve a submission endpoint](#serve-a-submission-endpoint-serve---ors-shipped).
* **One kernel = one admitted environment**, many ephemeral sessions, one shared
  registry and engine seam, and no lock held across a session lifetime — so a
  future server is not serialized down to one episode at a time.
* **A session finalizes exactly once, ever.** `finalize` *claims* the session
  under the table lock (`started → finalizing`) and only then does the scoring
  work, outside the lock; a second concurrent caller is refused by name rather
  than allowed to re-run the plan, and `close` on a session mid-flight is
  refused with `KERNEL_SESSION_BUSY`. This is not a theoretical hardening: a
  short lock is not an atomic transition, and two callers who both re-ran a
  Residency task's test suite would have received the *same receipt* — the
  identity does not move, only the work doubles. A failed finalization is
  **terminal** and cannot be retried; verifiers have external effects, so the
  honest recovery is a new session, which is a new episode and says so.

Keeping the kernel neutral means [Open Reward Standard](https://openreward.ai)
or MCP protocol evolution never becomes TRVM runtime law. The ORS wire surface
(`list_tasks · session · call_tool → reward · finished`) and MCP primitives
(`tools · resources · prompts`) are projections of the same kernel.

**Strategy:** do not compete with hosting catalogs. TRAAVIIS aims to be one of
the best ways to *author* deterministic environments that export to them.

### The bundle — `traaviis.environment.v1`

`trvs pack` separates *what an environment means* from *how it is shipped*. The
**environment manifest** (`env-…`) fixes the world, tasks, rewards, action /
observation profiles, and split membership; the outer **package** (`bundle-…`)
is the content address of the whole shipped tree — `env-…` plus every
presentation, doc and screenshot member by path, bytes and canonical mode — so
it may change without moving `env-…`. A package is **closed** — the embedded world re-lowers to its declared
`sem-…` and every task / reward / scenario reference resolves inside the closure,
or `pack` fails loudly (it re-opens and re-verifies the emitted bundle before
reporting success).

TRAAVIIS freezes an **artifact ladder**, each level answering one question:

| id          | question                        |
| ----------- | ------------------------------- |
| `sem-…`     | was it the same subject?        |
| `scen-…`    | same initialization?            |
| `rew-…`     | same scoring rubric?            |
| `task-…`    | same assignment?                |
| `trace-…`   | same behavior?                  |
| `episode-…` | same evaluated outcome?         |
| `env-…`     | same environment release?       |
| `bundle-…`  | same distributed package tree?  |

A **`trace-…`** is the substrate-neutral observable record; a **`film-…`** is the
*TRVM case* of a `trace-…`. Re-scoring the *same* trace under a different rubric
changes the `episode-…` receipt but never the `trace-…` — the recorded behavior
did not change. The shared evaluation constructs live in the `traaviis.*`
namespace; substrate-specific evidence (TRVM `sem·scen·film`, Residency
`snap·trace·finding·patch`) lives below.

### Evaluation before training

The first job is not a trainer, and the first interface is not a batch — it is a
one-shot `trvs eval-one task.json --agent-command …` over a single frozen
subject. Batch `trvs eval` over a split (with a side-by-side comparison view)
follows once eval-one is boring. Because every episode is verified and
content-addressed, the numbers are reproducible and the traces are re-checkable.

## Flagship worlds

- **Golden Spinner** *(shipped)* — the identity, replay and triple-fold
  tutorial. Installs, verifies, and reproduces byte-for-byte.
- **Evidence Residency** *(next)* — an agent inspects a frozen repository, finds
  one real spec/implementation inconsistency, cites the conflicting evidence,
  proposes the smallest patch, runs the declared checks, and returns a structured
  finding + a re-verifiable receipt. The first end-to-end evaluation environment;
  see `RFC_EVIDENCE_RESIDENCY.md`.
- **Courier / Factory** *(planned)* — move objects, open gates, route signals,
  spend energy, obey safety constraints, complete deliveries: spatial state,
  long-horizon tasks, objective rewards, resets, splits.
- **WallRider / Graffiti** *(planned)* — an agent moves through a city and writes
  executable tags that alter surfaces, routes and permissions. It proposes graph
  edits; TRVM decides what they mean.

## The product boundary

The seams are frozen on purpose:

| layer            | responsibility                                          |
| ---------------- | ------------------------------------------------------- |
| **TRAAVIIS**     | the product — CLI, environment surface, evaluator, packaging |
| **trvs**         | the command-line interface                              |
| **TaskSpecV1**   | the substrate-neutral assignment + evaluation contract  |
| **WallRiderLang**| the language for TRVM worlds, actors and world rules    |
| **Forge**        | the compiler, identity and artifact pipeline            |
| **TRVM**         | the deterministic execution substrate                   |
| **Spinner Bench**| the reference workbench and conformance laboratory      |

## What TRAAVIIS is not

- **Not a coding agent** — it builds and proves environments; it is not another
  chat/coding assistant.
- **Not a model router** — nothing here calls an LLM or picks a provider. The
  fold is deterministic; the reward is computed.
- **Not an RL cloud** — worlds run locally; TRAAVIIS authors environments that
  export to hosting stacks, it is not the catalog.
- **Not a game engine** — WallRider worlds are deterministic agent environments,
  not a rendering or physics engine.
- **Not the language or engine** — WallRiderLang defines a world; TRVM/Forge
  lower and fold it. `trvs` holds no world semantics.
- **Not trust-me** — every film is checked by every applicable verifier and
  content-addressed. Disagreement is exit 1, not a warning; a verifier that
  cannot apply is reported `not_applicable`, never as pass or fail.

## Develop

```sh
python3 tools/run_battery.py           # the whole battery, one total
python3 test/test_cli.py               # world CLI battery over the engine
python3 test/test_cli_evalone.py       # eval-one admission + episode battery
python3 test/test_kernel.py            # EpisodeKernelV1 lifecycle battery
python -m traaviis.cli doctor          # run from source
```

Every `test/test_*.py` is a self-running script, so any one of them can be run
alone. `tools/run_battery.py` runs them all and prints a single total; prefer it
over adding the per-file summaries up by hand, which is how one handoff came to
report two different totals for the same tree.

Releases go through an acceptance gate that extracts the packet into an empty
directory, runs the battery there both with and without the engine, and rebuilds
the packet with two different extractors to prove its hash does not depend on
the host:

```sh
python3 tools/build_packet.py  PACKET.zip
python3 tools/accept_packet.py PACKET.zip --forge /path/to/TRVM/forge
```

The Node harness that used to live at the repository root is quarantined in
`legacy/node-harness/` and is not part of this package, the battery, or the
release packet. See its README for why.

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
Home: **traaviis.com**.
