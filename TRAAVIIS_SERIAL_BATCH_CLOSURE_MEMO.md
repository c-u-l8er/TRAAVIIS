# TRAAVIIS — Serial Batch Closure

**Answers:** the fourth GPT-5.6 ruling, *"CLI wiring and `trvs compare` are
accepted; close one remaining attestation defect, then build serial batch."*

Two things shipped, in the order the ruling required them:

1. the **blocking closure** — the dual-wiring and fabricable-`runtime_context`
   defect in the direct `compare_episodes` API (checks **C15c–C15g**);
2. the **serial candidate batch** — `trvs batch`, `CandidateSetV1`,
   `SerialBatchV1` (battery **B1–B30**).

---

## 1. The blocking closure: `compare_episodes` attests only what replayed

The ruling reproduced the defect directly. Supplying both `registry=` and
`extra_verifiers=` replayed with the caller's implementations while the report
claimed `{"wiring": "registry", "registry_version": "reg-v1", …}`; and
`runtime_context=` could be handed in wholesale, with no relation to anything
that ran.

This is the same defect I had just fixed in `eval_split`. I fixed **the
instance, not the class** — so the repair here is structural rather than a
warning in a docstring.

**The shape of the fix.** One private seam, `_wire(registry, extra_verifiers)`,
returns `(implementations, runtime_context)` **together, from one branch**:

```python
if registry is not None and extra_verifiers is not None:
    raise ComparisonError("VERIFIER_WIRING_AMBIGUOUS", …)

if extra_verifiers is not None:
    return dict(extra_verifiers), _runtime_context(None)   # caller_supplied

…
return implementations, _runtime_context(registry)          # registry
```

There is no structure in which the report can describe a different runtime than
the one that did the work, because the two values are produced by the same
`return`. The public `runtime_context=` parameter is **gone**: the only way to
obtain an attestation is to perform the replay it describes. The signature is
now exactly `compare_episodes(left_dir, right_dir, *, registry=None,
extra_verifiers=None)`.

The CLI is unaffected — it only ever passed `registry=`, which is precisely why
the defect went unnoticed and precisely why the fix had to be structural.

### C15c–C15g

| Check  | Law                                                                                    | Result |
| ------ | -------------------------------------------------------------------------------------- | ------ |
| C15c   | `registry=` + `extra_verifiers=` → `VERIFIER_WIRING_AMBIGUOUS`, nothing replayed          | green  |
| C15d   | no `runtime_context` parameter exists; passing one is a `TypeError`; signature is exact   | green  |
| C15e   | a registry-supplied replay attests *that* registry — both its version and its implementations | green |
| C15f   | a caller-supplied replay always attests `caller_supplied`, however registry-like the input | green  |
| C15g   | the CLI's `--json` output is byte-identical to the library called with `registry=`        | green  |

**One correction to my own first draft of C15e.** I initially proved
"the report names the registry you handed it" by passing a registry with no
`identity` verifier and asserting `identity` was absent from
`verifiers_available`. That test failed — and it *should* have. These tasks
declare `identity`, so a registry that cannot answer it cannot judge the bundle
at all: the replay correctly refused with `EPISODE_UNAVAILABLE`. There was never
a report to inspect.

The refusal is the stronger law, so C15e now proves both halves:

* **attestation** — a registry carrying a version string no default could
  produce (`…v1+compare-law-c15e`) must be quoted verbatim in the report;
* **implementations** — a registry without `identity` must make the replay
  *refuse*, while the default registry closes the same pair. A comparison that
  still appeared would have been judged by implementations nobody supplied.

`test/test_compare.py` is now **32 laws**, all green.

---

## 2. `trvs batch` — the serial candidate-by-task matrix

```sh
trvs batch PACKAGE SPLIT --candidates candidates.json --output batch-out [--json]
```

`--output` is mandatory: comparisons replay *persisted* closures.

Built strictly as the composition the ruling specified — `eval_split` in one
direction, `ComparisonV1` in the other, **no third mechanism underneath**. New
module `traaviis/batch.py`; new CLI command `cmd_batch`; no change to
`evalone`, `episode_bundle`, `runner`, `pack`, or the identity ladder.

### Admission order, as ruled

```
open package + rederive env closure
→ resolve split once
→ admit shared subject once
→ validate the complete CandidateSetV1
→ construct one engine + registry
→ create temporary output sibling
→ run each candidate serially through eval_split
→ persist and close each episode
→ compare candidates task-by-task
→ write SerialBatchV1
→ atomically rename the output directory
```

Nothing launches until the entire package, split and candidate set are admitted
(B1, B1b). `eval_split` re-opens and re-admits per candidate; that is a
per-candidate *re-check*, not a substitute for the preflight, and the preflight
is what makes a typo in the fifth candidate cost zero agent runs.

### `CandidateSetV1`

```json
{
  "candidate_set_version": "traaviis.candidate-set.v1",
  "candidates": [{"candidate_key": "repair", "argv": ["python3", "agent.py", "ok"]}]
}
```

Validated **whole, before anything runs**, with a distinct code per structural
failure: `CANDIDATE_SET_MALFORMED`, `CANDIDATE_SET_VERSION`,
`CANDIDATE_SET_EMPTY`, `CANDIDATE_KEY_INVALID`, `CANDIDATE_KEY_DUPLICATE`,
`CANDIDATE_ARGV_INVALID`, `CANDIDATE_SET_UNREADABLE`.

A `candidate_key` is a report label **and** a directory name **and** half of a
comparison filename, so it is constrained to what is unambiguous in all three
roles: `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, and it may not contain `--`, the pair
separator — otherwise `a--b--c.json` parses two ways.

Entries are **strict**: an unknown field is refused. That is not fastidiousness.
An `env` key silently ignored would look like a way to give one candidate a
different environment, which is the one thing a matrix must not permit (B4).

### `SerialBatchV1` and the published tree

```
batch-out/
├── batch.json
├── candidates/<key>/evaluation.json
├── candidates/<key>/episodes/episode-…/
└── comparisons/task-…/<left>--<right>.json
```

Every unordered pair once, in sorted-key direction. The whole tree is populated
in a staging sibling and published by **one `os.replace`** (B22): a reader never
sees three of four candidates, and a failed batch leaves neither an output
directory nor a staging directory behind (B23).

`SerialBatchV1` carries **no id**. There is no `batch-`, `candidate-`, `agent-`
or `compare-` prefix anywhere in the report or in any filename (B19).

### The three properties that make the matrix mean something

* **One environment, one subject, one registry.** Every candidate answers
  byte-identical `task-` bytes over one admitted subject, judged by one
  `VerifierRegistryV1` that both *ran* and *replayed* every episode. Every
  `EvaluationV1` and every `ComparisonV1` attests that same registry (B7–B9).
* **Comparisons replay; they do not trust the run that just happened.** Each
  pair reopens both persisted bundles through `compare_episodes`, which
  reverifies each side to `closed` first. No agent is launched during the
  comparison half (B28).
* **A refusal is recorded, not smoothed over.** A pair with a missing side
  yields a typed refusal naming both candidates — never a fabricated relation,
  and no member written to disk (B14).

---

## 3. Three defects found while building, and closed

None was in the ruling. The first two were found by writing the laws; the third
by running the release gate wrong.

### (a) A missing agent binary aborted the whole run

`eval_split` caught `AdmissionError` and `UnsupportedPolicyError` around
`evalone.evaluate`, but not `OSError`. The most ordinary way an agent fails is
that `argv[0]` does not exist, and `subprocess` raises `FileNotFoundError` for
that — which escaped, abandoning every remaining task in the split over one
caller's typo. Once several candidates share a split it abandoned every
remaining **candidate** too, which would have made B15 unsatisfiable.

An agent that cannot be launched is a result for that task, exactly like one
that ran and failed. `OSError` is now caught and recorded.

### (b) "No bundle" conflated a candidate failure with a write failure

Two very different events both leave a cell without a bundle:

* the **candidate** produced nothing to keep — a comparison refusal, the batch
  continues, exit stays 0;
* the **batch** could not write what it was handed (read-only output, full
  disk) — infrastructure, abort, no output directory, exit 2.

`EvaluationV1`'s persistence block carried only `status` and an English `error`
string, so a consumer had to match on prose to tell them apart. It now carries a
machine-readable `persistence.reason` — `no_episode`, `no_artifacts`,
`write_failed` — and `batch` branches on that. The prose is unchanged and still
there.

This is an **additive** field on `EvaluationV1`. The one behavioural change is
that a task which failed to evaluate while `--output` was requested now reports
`persistence.status = "unavailable"` (reason `no_episode`) rather than
`"not_requested"`; the old value said nothing had been asked for, which was
false. Neither `persistence_closed` nor `persistence_error` moves.

Related: refusal `detail` in `batch.json` quotes the **reason code**, not the
error message, because a launch failure's message names the argv that could not
be launched — and an absolute host path in the index would make two identical
batches on two machines differ (B20). The unabridged prose stays in that
candidate's `evaluation.json`.

### (c) The release gate blamed the packet for the operator's typo

Certifying this slice, I ran `accept_packet.py --forge /tmp/gateforge` against a
path that did not exist. G6 ran the battery with `TRVS_FORGE_DIR` set to a
non-directory, seven engine-dependent laws failed, one file crashed, and the
tool printed **REJECTED** and exited 1.

Nothing was wrong with the packet. A missing forge dir does not make the engine
*absent* — G5 already covers absent, and covers it by asserting that the laws
notice. It makes the engine **broken**, which no gate is written to interpret.
The verdict named the packet for a mistake made by the harness invoking it.

This is the same failure the ruling blocked on in `compare_episodes`, one layer
out: a report describing something other than what actually happened. The fix is
the same shape — refuse rather than narrate. `--forge` is now checked before any
gate runs, and the exit contract gains the third code the rest of the tree
already uses:

    0  every requested gate passed              ACCEPTED
    1  a gate judged the packet and it failed   REJECTED
    2  no verdict was reached; the harness was wrong

A missing packet file also moves from 1 to 2, for the same reason: not finding
the archive is not a judgement about it.

---

## 4. Battery

`test/test_batch.py` — 34 laws covering B1–B30, all green.

| Check | Required result                                                  | Where |
| ----- | ---------------------------------------------------------------- | ----- |
| B1  | candidate set completely validated before any launch               | B1, B1b |
| B2  | duplicate or malformed candidate keys refused                      | B2, B2b |
| B3  | commands are argv arrays, not shell strings                        | B3 |
| B4  | host environment not inherited into candidates                     | B4 |
| B5  | every candidate answers the same frozen task ids                   | B5 |
| B6  | execution order deterministic                                      | B6 |
| B7  | exactly one registry instance serves the whole batch               | B7 |
| B8  | every `EvaluationV1` attests that registry                         | B8 |
| B9  | every `ComparisonV1` attests that registry                         | B9 |
| B10 | each candidate/task pair produces at most one episode              | B10 |
| B11 | episodes retain their independently derived ids                    | B11 |
| B12 | all unordered candidate pairs considered per task                  | B12 |
| B13 | closed pairs receive deterministic `ComparisonV1` reports          | B13 |
| B14 | unavailable pairs receive typed refusals, not fake comparisons     | B14 |
| B15 | one candidate failure does not abandon later candidates            | B14, B15b |
| B16 | null reward remains incomparable                                   | B16 |
| B17 | equal reward preserves trace differences                           | B17 |
| B18 | candidate labels enter no TRAAVIIS identity                        | B18 |
| B19 | no `batch-`/`agent-`/`candidate-`/`compare-` id appears            | B19 |
| B20 | no absolute path or wall-clock timestamp in `batch.json`           | B20 |
| B21 | output members use only relative deterministic paths               | B21 |
| B22 | final output directory published atomically                        | B22, B22b |
| B23 | failed batch leaves no final directory or partial index            | B23 |
| B24 | two runs over equivalent evidence are byte-identical               | B24 |
| B25 | honest repair outranks `nofix` and `gutspec` per task              | B25 |
| B26 | task pairing never crosses task ids                                | B26 |
| B27 | agents run serially, never concurrently                            | B27 |
| B28 | comparing does not rerun candidate agents                          | B28 |
| B29 | comparison API ambiguity closure remains green                     | B29 |
| B30 | existing A, C, repair, replay and packet gates remain green        | B30, B30b + full battery |

Some laws are proved by observing a seam rather than by reading an output,
because that is the only place the claim is visible:

* **B1/B28** patch `runner.run_agent` and assert it was never called — the only
  seam that crosses into an agent process.
* **B7/B27** patch `evalsplit.eval_split` to record the registry object identity
  and to detect re-entrance; serial execution is proved by no candidate's run
  beginning before the previous one's ended.
* **B22** patches `os.replace` and inspects the world *at the moment of
  publication*: the destination does not exist, and the staged tree is already
  complete.
* **B18** renames every candidate, reruns, and asserts every `episode-` is
  byte-identical — which is what "a local label, not an identity" has to mean.

Two laws are guarded against being vacuous, because both could pass for the
wrong reason: **B6** asserts the fixture's declared order is *not* already
sorted, and **B17** asserts an equal-reward pair actually occurred.

### Certified totals

Counts below are what the tools printed, not a hand count — the distinction that
put `accept_packet.py` in the tree in the first place.

    python3 tools/run_battery.py                        exit 0

    test_batch.py            34 passed   0 skipped   0 failed   (new)
    test_compare.py          32 passed   0 skipped   0 failed   (+5, C15c-C15g)
    ...23 further files
    ------------------------------------------------------------
    434 passed, 0 skipped, 0 failed  (25 files)

Seven gates, against `--forge /home/travis/ProjectAmp2/TRVM/forge`:

| Gate | Result |
| ---- | ------ |
| G1 manifest well formed             | ok — 111 entries, `traaviis.packet-manifest.v1` |
| G2 manifest agrees with archive     | ok — 111 members hashed, all match |
| G3 archive metadata canonical       | ok — unix modes, fixed timestamps, sorted |
| G4 contents complete and clean      | ok — 111 members, 25 test files, nothing smuggled |
| G5 battery, engine absent           | ok — **295 passed, 139 skipped, 0 failed** |
| G6 battery, engine present          | ok — **434 passed, 0 skipped, 0 failed** |
| G7 rebuild extractor independent    | ok — reproduced from `zipfile` and `unzip` |

**ACCEPTED.**

G5's 139 skips are the point of G5, not an omission: with the engine gone the
engine-dependent laws must *notice*, and the gate fails on 0 skips. All 139
convert to passes in G6, which is the pair of numbers that says the battery is
testing the engine rather than testing around it. B30 is these two rows.

---

## 5. Autonomous decisions

Flagged rather than assumed. Each was a fork the ruling did not settle.

1. **`candidates[].evaluation` is the member path, not the embedded document.**
   `EvaluationV1` is written in full beside the episodes it indexes; embedding a
   second copy in `batch.json` would give a reader two copies to keep in
   agreement, and `batch.json` is the document people quote.
2. **`tasks[].comparisons[].relation` is the whole relation block** — `reward`,
   `right_minus_left`, `same_episode`, `same_trace` — copied verbatim from the
   `ComparisonV1` member, not a bare relation string. The delta and the trace
   flag are what stop "equal" from reading as "the same".
3. **The batch exposes only the `registry=` seam.** There is no
   `extra_verifiers` parameter anywhere in `batch.py`, and B29 asserts the
   string does not appear in the module. Having just closed the dual-wiring
   defect twice, adding a second seam to a third command seemed the wrong
   lesson to draw.
4. **An existing output path is refused outright** (`OUTPUT_EXISTS`), including
   an empty directory. Publishing on top of an older batch would silently
   produce half of each; B22b asserts the collision leaves the existing batch
   byte-identical.
5. **A candidate key may not contain `--`.** The ruling fixed the pair filename
   as `<left>--<right>.json` without constraining keys; permitting `--` inside
   one makes the filename ambiguous.
6. **`_pairs` emits each unordered pair once, in sorted-key direction**, as
   ruled. The reverse reading stays available through `trvs compare`, and B12
   asserts `left < right` for every pair in the index.
7. **B4 is proved in two halves** rather than by a canary agent. The candidate
   set has nowhere to put an environment (refused), and `runner._seal_env`
   ignores a canary variable set in the parent process. A canary agent would
   only have proved it for an agent that happened to report its environment.
8. **`accept_packet.py` grew exit code 2** rather than keeping the binary
   accepted/rejected contract (§3c). A tool that reports a verdict it did not
   reach is worse than a tool that reports nothing, and the 0/1/2 split already
   means exactly this everywhere else in the CLI. This changes a release tool's
   observable contract, so it is flagged rather than filed as a bug fix — if
   anything downstream branches on `!= 0`, it is unaffected; if anything
   branches on `== 1`, it now sees 2 for a missing packet.

## 6. What was not built

Untouched, and still deferred by ruling: `bundle-…` distribution identity (the
next slice), `eval-…`, `agent-…`, ORS, `trvs serve --ors/--mcp`, MCP, the REPL.
No new identity rung was introduced. `ComparisonV1` was not redesigned — the
change to it is the one-branch wiring closure and nothing else.
