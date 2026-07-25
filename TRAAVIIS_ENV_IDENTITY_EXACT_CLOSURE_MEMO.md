# Environment Identity and Split Evaluation — Exact Closure

**Status:** shipped · **Battery at this slice:** 340 green, 0 failed, 0 skipped · **Date:** 2026-07-24

> **Counts below are pinned to this slice, not to the current tree.** Two later
> slices added laws, so the battery a reviewer runs today reads **358 / 0 / 0**
> with an engine and **292 / 66 / 0** without one. The numbers in this memo are
> left as they were rather than quietly rewritten, because a memo that silently
> tracks the tree is how the 335/340 drift happened in the first place. For the
> current totals see `TRAAVIIS_PACKET_AND_REPAIR_CLOSURE_MEMO.md`, or just run
> `python3 tools/run_battery.py`, which is now the only sanctioned way to count.

This memo answers the GPT-5.6 ruling that authorized the slice *"Environment
Identity and Split Evaluation Exact Closure"*. It records what was built for each
of the nine ruled items, the thirteen required laws (delivered as twenty-one),
the defects found along the way that the ruling did not ask for, and the
decisions taken autonomously that a reviewer may want to overturn.

The packet accompanying this memo is **standalone**, not a delta. Extracting it
into an empty directory and running the battery reproduces the same totals
without reference to any earlier archive:

| run | passed | skipped | failed |
| --- | --- | --- | --- |
| working tree, engine on-path | 340 | 0 | 0 |
| clean extraction, engine on-path | 340 | 0 | 0 |
| clean extraction, engine absent | 284 | 56 | 0 |

> **Erratum.** This paragraph originally claimed the clean extraction reproduced
> "335/335" while the header of this same memo said 340 — a number nobody
> computed twice the same way, because the battery had no single runner and the
> total was being added up by hand across twenty-one self-running files that do
> not even agree on a summary format. The count is now produced by
> `tools/run_battery.py`, which counts the per-test `PASS`/`FAIL`/`SKIP` lines
> every file already emits, and is checked by `tools/accept_packet.py` gates G5
> and G6. Both numbers above come from that runner. See the Packet and
> Repository Release Closure memo.

---

## 1. What the ruling was answering

The ruling accepted the `init → pack → eval-one / eval → persisted episode
bundles → verify-episode` direction and then made three corrections and one
accusation:

* **Correction A.** My claim that a content-addressed evaluation *result*
  necessarily requires an `agent-…` rung was wrong. A result could be
  `eval-… = hash(env_id, split, ordered {task_id, episode_id}, aggregation_profile)`
  and needs no agent identity at all; only an evaluation *plan* — a statement of
  what *will* be run — needs an `AgentSpecV1`. `EvaluationV1` stays
  non-content-addressed for now, and neither `agent-…` nor `eval-…` is introduced.
* **Correction B.** My sentence *"a task's verifier set is a function of the task,
  never of the caller"* was too strong. The precise law separates three layers,
  and §4 below implements that separation rather than the slogan.
* **Accusation.** The submitted archive was a **delta packet**. 314/314 could not
  be independently reproduced from it, and `traaviis.test-plan.v2` could not be
  verified from it at all — so the version label was, from the reviewer's seat,
  unproven.

Everything below is written so that a reader with only the packet can check it.

---

## 2. The nine ruled items

### 2.1 Canonicalize task and reward sets (`pack.py`)

An environment is a closed **set** of tasks, rewards and split members. A set has
exactly one written order, so `pack` now sorts:

```python
tasks.sort(key=lambda e: e["task_id"])
rewards.sort(key=lambda e: e["reward_id"])
```

The sort happens **after** `by_ref` is populated, not before. The previous code
paired source references to derived documents positionally
(`zip(manifest_src["tasks"], tasks)`); sorting first would have silently
mispaired every split member. `by_ref[ref] = doc["task_id"]` is now built inside
the loop, so split resolution stays keyed on the author's reference while the
manifest is stored in derived-id order.

### 2.2 Enforce canonical order on reopen (`substrates.py`)

Sorting in `pack` is not sufficient, and this is the load-bearing half of the
item. A manifest can be hand-built, be internally consistent — every id
re-derives from its bytes, every split member resolves — and still be unsorted.
Under the old code it would reopen cleanly. That gives one environment as many
admissible identities as its task list has permutations, which is precisely the
thing `env-…` exists to deny.

`_verify_canonical_manifest` is therefore called from `open_package`, and it is
called **before** the `env_id` comparison so the failure reads as a malformed
*form* (`MANIFEST_NONCANONICAL`) rather than a confusing identity mismatch.

### 2.3 Reject duplicate refs and duplicate derived ids (`pack.py`)

Three distinct collisions, three distinct typed codes:

| code                    | what it catches                                              |
| ----------------------- | ------------------------------------------------------------ |
| `TASK_DUPLICATE_REF`    | the same source file listed twice (an author's copy-paste)    |
| `TASK_DUPLICATE_ID`     | two *different* sources collapsing to one `task-…`            |
| `REWARD_DUPLICATE_REF`  | as above, for rewards                                         |
| `REWARD_DUPLICATE_ID`   | as above, for rewards                                         |
| `MEMBER_COLLISION`      | two documents claiming the same path inside the package       |

The `_ID` cases are the interesting ones: two files with different names but
semantically identical content derive the same id, and admitting both would put
the same task in the set twice under the pretence of being two tasks.

### 2.4 `VerifierRegistryV1` (`wiring.py`, rewritten)

See §4 — this is Correction B, and it deserves its own section.

### 2.5 Bind identity to the selected Forge runtime (`forge_adapter.py`)

`real_adapter()` became `real_adapter(forge_api=None)`. When the caller passes
the engine it already selected, the identity verifier re-lowers through *that*
checkout. Only when nothing is passed does the soft loader (`engine.try_load`)
independently discover one. The old behaviour — always rediscovering — meant a
command could pack against one engine and verify identity against another
without saying so.

### 2.6 Requested-persistence failure is operationally unavailable

`evalsplit.py` now carries two independent outcomes per entry:

```python
{"status": …, "reward": …,
 "persistence": {"requested": bool(output), "status": …, "error": …}}
```

`status`/`reward` describe the *evaluation*; `persistence` describes *retention*.
Conflating them once let a run report `ok` for an episode whose bundle had failed
to write — a score with no retained proof, printed as though the proof existed.
Totals gained `persistence_closed` / `persistence_error`, and `cmd_eval` reads
them in precedence:

```
persistence_error → 2   (could not keep what was asked for)
ok != tasks       → 1   (ran and disagreed)
otherwise         → 0
```

### 2.7 TestPlanV2 — proven, then corrected

The accusation offered two options: the V2 label is real but was omitted from the
packet, or it is aspirational. The honest answer was a third.

Grepping the working tree showed `TEST_PLAN_V2`, `_is_v2`, `_validate_command_v2`,
`test_plan_tools` and `_command_argv` all present and exercised — so V2 *tool
resolution* is genuinely implemented, and the reviewer's option 1 is correct as
to that half. But the semantics the ruling was now specifying — per-phase
`allowed_exit_codes` — were genuinely absent: the verdict hardcoded
baseline-all-0 and patched-all-0. **The version label was real and implemented a
documented but narrower contract than the one being ruled.**

That gap mattered beyond conformance. Under the hardcoded rule, a nonzero
baseline made the *fixture* inadmissible, so a task could not express *"this test
must fail before the patch and pass after"* — the single most common shape of a
real repair, and precisely the shape the ruling's own roadmap (a genuine
spec/implementation repair task) depends on.

Implemented as ruled:

```json
{"tool": "pytest", "args": ["-q", "test/test_bug.py"],
 "baseline": {"allowed_exit_codes": [1]},
 "patched":  {"allowed_exit_codes": [0]}}
```

Both default to `[0]`, so an undeclared plan keeps the exact V1 meaning (pinned by
law X13b). Every emitted record now carries the `expected_exit_codes` it was
judged by, so the evidence states its own rule rather than requiring the reader to
know which version of the verifier produced it (law X13c). The asymmetry the
ruling specified is preserved: baseline miss → `error` (the fixture is
inadmissible), patched miss → `fail` (the candidate is wrong).

### 2.8 Fixtures regenerated

Adding `expected_exit_codes` to the tests evidence changes the evidence bytes,
which are sealed into `episode-…`. That is the system working: the episode id
*should* move when the rule the evidence was judged by changes.

Confirmed rather than assumed — the shipped fixture was run through
`trvs verify-episode` first and reported exactly one honest failure:

```
tests       ✗ replay=pass receipt=pass (evidence digest mismatch)
episode-id  ✗ episode_id mismatch
verified    ✗ failed: receipt, episode_id, signal:tests
```

Regenerated: `episode-bcd4e794…` → `episode-42d0bb07e5f83e9e57518bf5cd3717e2a1e3aa45aa5821e36ac19206a3d73299`,
which verifies closed with reward 1.0 and all five Residency signals passing.

**`snap-…`, `rew-…` and `task-…` did not move.** Re-running the generator
reproduces `snap-c66198ab…`, `rew-25c4ce12…`, `task-52bd629f…` byte for byte. Only
the episode moved, which is the correct blast radius: the change was to how a
verifier reports, not to what a task *is*.

### 2.9 Standalone packet

`TRAAVIIS_ENV_IDENTITY_EXACT_CLOSURE_PACKET.zip` contains the complete package,
the complete test tree, the examples, and the docs. It is not a delta. §6 records
the acceptance procedure a reviewer can run against a clean extraction.

---

## 3. The required laws

The ruling required thirteen additions. `test/test_exact_closure.py` delivers
twenty-six; the thirteen extra are marked ✚.

| law   | statement                                                        |
| ----- | ---------------------------------------------------------------- |
| X1    | task source-order change does not move `env-…`                    |
| X2    | reward source-order change does not move `env-…`                  |
| X3    | an unsorted but self-consistent manifest is rejected on reopen    |
| X3b ✚ | an unsorted **split** is rejected on reopen                       |
| X4    | duplicate canonical task id is rejected                           |
| X5    | duplicate canonical reward id is rejected                         |
| X6 ✚  | duplicate *source reference* is rejected (task and reward)        |
| X7    | explicit engine context controls the identity verifier version    |
| X8    | CLI and library runs seal identical verifier versions             |
| X8b ✚ | the declared plan is a pure function of the task                  |
| X9    | episode persistence failure does not exit 0                       |
| X9b ✚ | successful persistence is counted                                 |
| X9c ✚ | retention is *reported* even when it succeeded                    |
| X10   | a low but valid reward still exits 0                              |
| X11   | TestPlanV2 baseline-fail / patched-pass works                     |
| X12   | baseline expectation mismatch is `error`                          |
| X13   | patched expectation mismatch is `fail`                            |
| X13b ✚| undeclared expectations keep the V1 meaning                       |
| X13c ✚| the evidence states the rule it was judged by                     |
| X13d ✚| a malformed expectation is a typed fixture error                  |
| X14   | fresh packet runs from clean extraction (see §6)                  |
| X15 ✚ | a split typo is diagnosed without an engine                       |
| X15b ✚| an unanswerable plan is diagnosed without an engine                |
| X15c ✚| a non-empty destination is diagnosed without an engine             |
| X15d ✚| a preflight refusal writes nothing                                 |
| X15e ✚| the preflight does not mask a real engine requirement              |

X3 is written carefully: it recomputes `env_id` honestly over the reordered bytes
*before* attempting the reopen, so it proves the manifest is refused **on form**
and not merely because a stale identity failed to match. A test that skipped that
step would pass against a system with no canonicality check at all.

X14 does double duty. As written in the file it asserts the package imports under
*eager* annotation evaluation — see §5.1 — and §6 is its clean-extraction half.

---

## 4. Correction B, implemented

The ruling replaced my slogan with a three-layer law, and the code now names all
three:

| layer                     | function of              | where                       |
| ------------------------- | ------------------------ | --------------------------- |
| declared plan             | the task alone           | `wiring.declared_signals`   |
| available implementations | task + runtime/registry  | `VerifierRegistryV1`        |
| sealed history            | what actually answered   | `receipt.verifier_versions` |

`declared_signals(task)` is pure and does no I/O, so two callers reading the same
task always compute the same plan (law X8b). `default_registry(engine)` builds the
implementations **once per command** from the engine that command selected, so
every task in a split is answered by the same verifiers bound to the same
checkout. If the engine is unreachable, `identity` is left declared-but-unwired
and reported as a note — never silently dropped, never faked into a `pass`.

This also collapsed a genuine duplication: `cli.py` had **three** separate
verifier-wiring paths (`eval-one`, `eval`, `verify-episode`), each free to drift
from the others. They are now one registry built in one place, which is what makes
law X8 — CLI and library seal identical versions — true by construction rather
than by coincidence.

---

## 5. Defects found that the ruling did not ask for

### 5.1 The package was unimportable on Python ≤ 3.10

`substrate_verifiers.py` annotated `Optional[Mapping[str, str]]` while importing
only `Any, List, Mapping, Tuple`. On Python 3.14, PEP 649 lazy annotations mean
the module imports and runs fine — which is why all 314 tests were green. On
Python ≤ 3.10 the module fails to import outright, and even on 3.14
`inspect.signature`, `typing.get_type_hints` and `help()` crash today.

Verified by execution rather than assumed:

```
$ python3 -c "import inspect, traaviis.substrate_verifiers as SV; \
              inspect.signature(SV.run_command_set)"
import OK
NameError: name 'Optional' is not defined
```

Fixed, then an AST scan over all 24 modules confirmed zero remaining instances of
the same class of bug. Law X14 now walks every module with `get_type_hints`, so a
green suite can no longer hide this.

This is worth flagging as a *methodology* finding, not just a bug: **a fully green
battery on one interpreter proved nothing about importability on another.** The
law was added because the test suite's own success was the thing that concealed
the defect.

### 5.2 `OSError` abandoned the rest of the split

`_run_one` caught only `EpisodeBundleError`. A read-only output directory or a
full disk — the two most ordinary ways retention fails in practice — would raise
out of `eval_split` and abandon every remaining task. Found while reasoning about
how to *test* X9, which is the honest provenance: the law came first and the
defect surfaced because the law had to be satisfiable.

Now caught alongside `EpisodeBundleError` and recorded as that one task's
persistence error, so the split continues and the failure is a reported outcome.

### 5.3 `bundle.json` could name the wrong reward

`pack.py` hardcoded `reward_path = rewards[0]["path"]`, correct only when exactly
one reward is declared. With multiple rewards and one task it named a reward the
task does not bind. Now looked up from the binding the task actually declares.

### 5.4 Retention reported only on failure

Found during the end-to-end smoke, after the code was otherwise complete: the
`episodes kept` line printed only when something failed. That makes *silence* the
sole signal for "the evidence is still there" — unauditable, and the same
conflation the persistence outcome had just been split apart to end. Now printed
whenever retention was requested, and still silent when it was not, so the line
means "your request was met" rather than "we happened to write something". Pinned
by law X9c.

---

### 5.5 An author's mistake was diagnosed as a broken machine

**This one was found by the clean extraction itself**, which is the strongest
argument for the ruling's item 9. The home tree had an engine on-path, so
`test_pack` was 18/18 there. The first clean extraction — no `TRVS_FORGE_DIR` —
reported three failures:

```
FAIL test_p4_split_naming_an_unknown_task_is_refused: ENGINE_UNAVAILABLE
FAIL test_p4_unanswerable_verifier_plan_is_refused_before_write: ENGINE_UNAVAILABLE
FAIL test_p5_non_empty_destination_is_refused: ENGINE_UNAVAILABLE
```

One cause. `pack` follows §6 literally — validate subject, recompute identity,
bind, close, write — and identity recomputation reaches for the Forge engine.
That put a whole class of *author* errors behind an *environmental* prerequisite.
A first-time reader with no engine on-path and a typo in `env.json` was told
`ENGINE_UNAVAILABLE`: a true statement about their machine, and a useless one
about their environment. Worse than a wrong message, because it sends them to fix
the wrong thing.

The tempting fix was to make the three tests skip without an engine. That would
have deleted the evidence rather than the defect. The real fix is that **checks
needing only the author's own documents run before checks needing the world**:

* `substrates.verify_task_is_answerable(task, signals)` was extracted as a free
  function — it never needed a snapshot, derived ids, or an engine, only the two
  documents an author wrote. `_verify_task_closure` now delegates to it, so there
  is one implementation with two call sites, not a duplicated check.
* `pack._preflight` runs before §6 step 1: destination admissible, every split
  member names a listed task, every plan answerable by the reward it names.
* `_write_tree` still re-checks the destination. A preflight is not a lock, and
  the answer can change between the check and the write.

Pinned by X15–X15e. X15e is the one that matters most: a scaffold whose documents
are all correct *still* needs the engine and must still say so — otherwise this
fix would have traded a misleading error for a missing one.

---

## 6. Acceptance from a clean extraction

```sh
unzip TRAAVIIS_ENV_IDENTITY_EXACT_CLOSURE_PACKET.zip -d /tmp/trvs-clean
cd /tmp/trvs-clean
export TRVS_FORGE_DIR=/path/to/TRVM/forge

python3 tools/run_battery.py
```

The `for f in test/test_*.py; do python3 "$f"; done` loop this section used to
show is what produced the erratum at the top: it prints twenty-one summaries in
two different formats and leaves the addition to the reader. Prefer the runner,
which prints one total. Better still, run the whole gate:

```sh
python3 tools/accept_packet.py TRAAVIIS_ENV_IDENTITY_EXACT_CLOSURE_PACKET.zip \
        --forge /path/to/TRVM/forge
```

Observed, twice — once in the working tree and once in a clean extraction of the
packet:

```
                                    working tree     clean extraction
with an engine on-path              340 / 0 / 0      340 / 0 / 0
without one                              —           284 / 56 skipped / 0
```

The packet cannot ship the Forge engine, so `TRVS_FORGE_DIR` is the one external
input. Without it the engine-dependent laws **skip**, and `identity` reports
`error` rather than `pass` — declared, unwired, and said out loud. Nothing
silently passes, and after §5.5 nothing fails for the wrong reason either.

End to end from the same clean extraction:

```sh
python3 -m traaviis.cli init /tmp/demo/env --template evidence-residency
python3 -m traaviis.cli pack /tmp/demo/env /tmp/demo/pkg
python3 -m traaviis.cli eval /tmp/demo/pkg --split all \
    --agent python3 test/fixtures/residency_agent.py \
    --platform linux-x86_64 --output /tmp/demo/episodes
python3 -m traaviis.cli verify-episode /tmp/demo/episodes/episode-49dce00f…
```

```
  ok            1/1
  mean reward   1
  episodes kept 1/1
  evaluation    /tmp/demo/episodes/evaluation.json

  verified      ✓ closed
```

Worth recording: the clean extraction produced **the same** `task-3b4b2599…` and
`episode-49dce00f…` as the working tree. Different directory, same bytes, same
ids — which is the whole claim, demonstrated rather than asserted.

`tools/build_packet.py` builds the packet deterministically (sorted entries,
fixed timestamps, normalized modes), so two builds of an unchanged tree are
byte-identical and the printed SHA-256 is a usable answer to *"which base was
this built on"* — the identifier the ruling asked future handoffs to carry.

---

## 7. Decisions taken autonomously

Flagged for review; each is reversible.

1. **`MANIFEST_NONCANONICAL` is checked before the `env-…` comparison.** A
   noncanonical manifest is a malformed form, not an identity mismatch, and
   reporting it as the latter would send an author looking for the wrong bug. If
   the preference is that identity always speaks first, the two calls swap.
2. **Duplicate *references* and duplicate *derived ids* get separate codes.**
   They are different author errors — a copy-pasted line versus two files that
   mean the same thing — and one code would have merged two diagnoses.
3. **`OSError` is caught as a persistence outcome, not propagated.** A full disk
   is a recorded failure for one task rather than an exception that abandons the
   split. If retention failure should be considered fatal to the whole run, this
   is the line to change.
4. **`episodes kept` prints whenever retention was requested**, including on
   success (§5.4).
5. **Per-phase expectations apply only to V2 plans.** V1 plans keep the literal
   old rule with no new field consulted, so no existing task's meaning moved.
6. **`expected_exit_codes` is written into every record.** This moves episode ids
   — as §2.8 shows — but the alternative is evidence that cannot be read without
   knowing which verifier version produced it.
7. **Booleans are rejected in `allowed_exit_codes`.** `bool` is an `int` subclass
   in Python, so `true` would otherwise be accepted as exit code 1. This is a
   deliberate narrowing of what the JSON admits.
8. **`pack` gained a preflight ahead of §6 step 1** (§5.5). The §6 order is
   otherwise followed literally; the preflight adds no new check, it only moves
   engine-independent ones earlier so they are reported as themselves. If §6's
   order is meant to be strict even at the cost of diagnosis, this is the change
   to revert — and X15e is the law that would then need deleting with it.

---

## 8. Not started, per the ruling

`trvs serve --ors`, the MCP adapter, and the REPL are explicitly **not** begun.
Neither `agent-…` nor `eval-…` was introduced, and `EvaluationV1` remains an index
with no id of its own. The ruled order after this closure is: a genuine repair
task → `trvs compare` → serial batch → `bundle-…` → serve/ORS.

---

## 9. Open questions for GPT-5.6

1. **Does the repair task now have everything it needs?** §2.7 makes
   baseline-fail/patched-pass expressible, which was the blocking gap. If the
   intended first repair task also needs multi-command phases with *different*
   expectations per command, that already works; if it needs a phase-level
   default distinct from `[0]`, it does not, and that is a schema addition.
2. **Should `EvaluationV1` record the registry it ran under?** Each episode seals
   its own `verifier_versions`, so the information is not lost — but reading it
   currently means opening every episode. A single index-level record would be
   convenient and would also be the natural place for the `aggregation_profile`
   that Correction A's `eval-…` formula would eventually need. I did not add it,
   because adding a field in anticipation of an id that has not been ruled seemed
   like the wrong direction to guess.
3. **Is `MEMBER_COLLISION` in the right layer?** It currently guards package
   member paths inside `pack`. If a substrate is ever allowed to choose its own
   member layout, the check belongs in the admission interface instead.
4. **Should the clean-extraction run be a gate rather than a ritual?** §5.5 was
   found because the packet was extracted and run *before* being handed over, and
   it would not have been found any other way — the working tree cannot see it.
   That suggests the extraction run belongs in the definition of done for every
   slice, not just the ones where a packet is explicitly requested. I have adopted
   it as a habit; whether it should be a stated requirement is a ruling.
