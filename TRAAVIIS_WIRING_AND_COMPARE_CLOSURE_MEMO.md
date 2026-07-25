# Verifier-Wiring Attestation, and `trvs compare`

**Status:** shipped · **Battery:** 395 green, 0 failed, 0 skipped · **Date:** 2026-07-24

This memo answers the third GPT-5.6 ruling. That ruling accepted the repair task,
the packet builder, and the multi-file patch correction, then **blocked** on one
defect and ordered it fixed before any new command was started:

> Before starting `trvs compare`, fix the normal CLI evaluation path so
> `EvaluationV1.runtime_context` attests the registry that actually supplied its
> verifiers, add a CLI-level regression battery, and correct the closure memo's
> 103/104 entry-count drift. Then implement `trvs compare` strictly as a
> side-effect-free, non-identity-bearing comparison of two independently closed
> episode bundles sharing one `task_id`.

All four are delivered, in that order.

| run | passed | skipped | failed |
| --- | --- | --- | --- |
| working tree, engine on-path | 395 | 0 | 0 |
| clean extraction, engine on-path | 395 | 0 | 0 |
| clean extraction, engine absent | 294 | 101 | 0 |

The bottom two come from `tools/accept_packet.py` gates G6 and G5, run against an
extraction of the delivered archive, not against this working tree.

**Packet:** `TRAAVIIS_WIRING_AND_COMPARE_CLOSURE.zip`
**Gate:** `ACCEPTED` — G1 through G7, from a clean extraction.

As before, this memo does not quote the packet's own SHA-256: the memo ships
inside the packet, so there is no fixed point to write. The authoritative digest
is the one `tools/build_packet.py` prints on build and `tools/accept_packet.py`
re-derives under G7 from two independent extractors.

---

## 1. The blocking defect: a false attestation on the only path users take

The ruling's diagnosis was exactly right, so it is worth restating in the form
that makes it a *class* of bug rather than a typo.

`eval_split` has two mutually exclusive seams:

* `registry=` — "take implementations from this registry", which the report then
  attests by name and version;
* `extra_verifiers_for=` — "the caller brought its own implementations", which
  the report must *disclaim*, because there is no registry to interrogate.

`cmd_eval()` needed to print a warning for each unavailable signal. The only hook
available was the wiring callback, so it built a real registry, wrapped it in a
closure that printed notes, and passed the closure as `extra_verifiers_for=`. The
library did precisely what it was told:

```json
{"wiring": "caller_supplied", "registry_version": null,
 "verifiers_available": [], "verifier_versions": {}}
```

Every implementation had in fact come from a registry, and the report denied it.
The direction matters: this is not an attestation that overclaims, it is one that
*disclaims provenance it had*, which is the harder failure to notice because
nothing downstream complains.

The library-level test passed throughout, because it called
`eval_split(..., registry=registry)` — the seam the CLI did not use. **A law that
does not enter through the same door as the user is not testing the user's path.**

### The fix

Notes became presentation, and stopped requiring ownership of the wiring:

```python
def eval_split(..., registry=None, extra_verifiers_for=None,
               on_wiring_notes=None):
    if registry is not None and extra_verifiers_for is not None:
        raise SplitError("VERIFIER_WIRING_AMBIGUOUS", ...)

    if extra_verifiers_for is None:
        wiring_source = "registry"
        registry = registry or wiring.default_registry(engine)
        extra_verifiers_for = _verifiers_from(registry, on_wiring_notes)
    else:
        wiring_source = "caller_supplied"
        registry = None
```

`_verifiers_from` calls the sink and returns the wiring unchanged, so the notes
never reach `EvaluationV1`. The CLI now passes `registry=` and `on_wiring_notes=`
and de-duplicates the notes itself, printing one line per *gap* rather than one
per task.

Both seams survive. Supplying both is refused rather than silently ranked, since
"which one wins" is not a question with a defensible answer.

### The battery: `test/test_cli_eval_wiring.py`, A1–A10

Every A-law runs `python3 -m traaviis.cli eval …` as a **subprocess**, because the
defect lived exactly in the gap between the library call and the CLI call.

| law | statement |
| --- | --- |
| A1 | a normal `trvs eval` reports `wiring: registry` |
| A2 | it reports the actual `VERIFIER_REGISTRY_VERSION`, not null |
| A3 | `verifiers_available` equals `registry.available()`, and is non-empty |
| A4 | `verifier_versions` equals `registry.versions()` |
| A5 | a runtime that cannot answer `identity` shows it as *absent*, not silent |
| A6 | human wiring warnings still print, once per gap, not once per task |
| A7 | notes do not enter `EvaluationV1` |
| A8 | genuinely injected verifiers still report `caller_supplied` and no registry |
| A9 | supplying both seams raises `VERIFIER_WIRING_AMBIGUOUS` |
| A10 | sealed `verifier_versions` agree with the attested runtime context |

A10 is the one that took three attempts and is worth reading. A receipt seals
`{contract, implementation}` per signal and includes the substrate-*independent*
verifiers (`citations`, `patch`, `finding_completeness`) that no registry
supplies. `runtime_context` describes only what the registry offered. The
consistency claim is therefore over the intersection, on the `implementation`
field:

```python
overlap = set(sealed) & set(ctx["verifiers_available"])
assert sealed[signal]["implementation"] == ctx["verifier_versions"][signal]
```

Requiring more than that would assert something false. The first two versions of
A10 did exactly that and were wrong, not the code.

---

## 2. The memo entry-count drift

`TRAAVIIS_PACKET_AND_REPAIR_CLOSURE_MEMO.md` illustrated the manifest with
`"entry_count": 103` while the delivered manifest declared 104. The count is
derived, so the memo was quoting a number the tree no longer produced. Per the
ruling's option, the illustration is now symbolic:

```json
{"manifest_version": "traaviis.packet-manifest.v1",
 "entry_count": "<payload-count>", ...}
```

with a note that the authoritative count is whatever G1 reads out of the
delivered archive. A literal there can only ever be right on the day it was
written.

That memo's battery totals are now explicitly **pinned to its own slice**, with a
pointer here for current numbers — the same discipline, applied before the drift
recurs.

---

## 3. `command_set_passed`

Suggested, not blocking, and taken. `substrate_verifiers` now exports:

```python
def command_set_passed(state) -> bool:
    """Did every command in the set exit as its phase expected?"""
    return state == _ALL_PASS
```

R3 in the repair battery used the underscored `_ALL_PASS`. It now uses the
predicate. This is a real improvement rather than cosmetics: `_ALL_PASS` is a
representation choice, and a test that pins it is asserting the representation
alongside the law it meant to state.

---

## 4. `trvs compare`

### What it is

```sh
trvs compare LEFT RIGHT [--json] [--output comparison.json]
```

Answers *"which of these two candidates did better on this task, and where did
they differ?"* over evidence that already exists. It runs **no agent**.

The admission order is the whole contract:

```
open left  -> verification-replay to CLOSED
open right -> verification-replay to CLOSED
require the same task_id
emit ComparisonV1
```

Requiring one `task_id` is what makes the two numbers mean the same thing. A
`task-…` fixes the task bytes, and through them the frozen subject and the reward
binding, so both episodes were scored by one rubric. Comparing rewards across two
tasks would be comparing two different questions.

### Why it mints nothing

`ComparisonV1` is an ordinary deterministic report. It carries **no id of its
own**: there is no `compare-…` rung, because a comparison is a *reading* of two
sealed episodes, not a new artifact anyone needs to re-derive. Everything in it
is already addressed by the ids it quotes. C20 enforces this by scanning the
serialized report and requiring every minted-looking id to belong to the existing
ladder.

### The two rules the reward relation must not break

**A null reward is `incomparable`, never zero.** An errored or unscored episode
did not score badly; it did not score. Imputing `0` would rank a fixture failure
below a bad-but-real attempt. The delta is withheld as well as the relation, so a
consumer cannot arithmetic its way past a missing score.

The end-to-end case for this is sharper than it first looks: compare a
null-reward bundle *with itself*. Both sides are byte-identical, so a naive
implementation reports `equal` — and that would be a lie, because neither side
scored. Two absences are not a tie. That is C9.

`bool` is rejected explicitly, because `True` is an `int` in Python and would
otherwise rank as `1.0`. A boolean is a verdict, not a score.

**Equal rewards do not hide a different trace.** There is no secondary
tie-breaker. `nofix` and `gutspec` both hit the tests cap at `0.4`, so under the
rubric they *are* equal; that they got there by different routes is reported as a
trace relation and as per-field differences, not folded into the ranking.

### The differences map

Five fields, each reported independently — collapsing them would hide *which*
kind of divergence occurred:

```
outputs · verification · verification_evidence · verifier_versions · execution_facts
```

Mappings are reduced to the keys that actually changed, so a reader sees the
signal rather than two nearly identical copies:

```json
"verification": {"tests": {"left": "pass", "right": "fail"}},
"outputs":      {"patch_id": {"left": "patch-2425eb0f…", "right": "patch-be2526be…"}}
```

Note what is *absent* there: `ok` and `gutspec` share a finding, so `finding_id`
does not appear at all. That is C12 — a patch change alone must name only the
patch.

### Exit contract

| code | meaning |
| --- | --- |
| 0 | both bundles closed and a comparison was produced (whichever side won) |
| 1 | one bundle reverified as an **evidence mismatch** |
| 2 | a bundle was unavailable/malformed, a verifier was unavailable, or the two answer different tasks |

The 1/2 split is the same distinction `verify-episode` draws. A bundle that
disagrees with its own evidence is a finding about that episode. A missing
bundle, an unreadable receipt, or two different tasks means this runtime could
not judge the pair — which is not a verdict against either candidate. A produced
comparison exits `0` even when the two sides differ wildly, because `compare`
reports a relation; it does not grade.

### The battery: `test/test_compare.py`, C1–C24

27 laws over four sealed bundles built once and shared. Three candidates
(`ok` / `nofix` / `gutspec`) answer one task; a fourth is the repair battery's R6
fixture-error episode, which is `error` with reward `None` and — because its
subject differs — necessarily answers a *different* task. That single bundle is
the honest input for both C3 (task mismatch) and C9 (null reward).

Two laws are worth calling out for how they are proved.

**C2, "no agent is launched."** `runner.run_agent` is the one seam that crosses
into an agent process. The law replaces it with a function that raises, then
compares; a comparison that still succeeds provably never called it. Replay
*does* spawn processes — the declared test commands — and that is exactly the
distinction being protected: those are verification, not the candidate.

**C21, "comparing does not disturb the episodes."** A SHA-256 over every byte
under each bundle, taken before and after two comparisons. Side-effect-freedom
stated as a digest rather than as an intention.

Also present: C1 (each side independently replayed, and the refusal names *which*
side), C16 (no host paths, no wall clock — a comparison must mean the same thing
on a machine that never had these directories), C17 (reversing the inputs
reverses the relation and negates the delta, while the symmetric relations do not
move), C18 (two readings are byte-identical), C19 (a failed write leaves neither
a stub nor a temp file), C22/C23 (the exit contract, through the real CLI), and
C24 (every bundle still reopens `closed` *after* every law above has read it).

---

## Decisions taken autonomously

Flagged for overturning.

1. **`compare` takes two bundle directories, not an evaluation index.** The
   ruling forbade comparing whole `EvaluationV1` indexes, and comparing a
   *bundle* is the smallest thing that can be independently reverified. A
   split-level comparison would need a rule for pairing tasks across two indexes,
   and that rule is not obvious enough to invent silently.
2. **A produced comparison exits 0 even when one side lost badly.** The
   alternative — exit 1 for "the candidates disagree" — would conflate "this
   command worked" with "the candidates tied", and would make a successful
   ranking indistinguishable from a tampered bundle.
3. **`registry.notes` are printed by `cmd_compare` before replaying.** Same
   treatment as `verify-episode`: a comparison judged with a missing identity
   verifier will refuse, and the reader should see why before the refusal.
4. **`ComparisonV1` quotes `subject` and `substrate_profile` from the left side.**
   They are equal by construction once `task_id` matches, so taking them from
   either is the same value; taking them from the left is arbitrary but stated.
5. **C24 re-verifies the fixture bundles rather than re-running the repair
   battery in-process.** The ruling asks that the repair and replay batteries stay
   green; that is a release-gate property and is measured by G5/G6 over a clean
   extraction. Re-running a battery from inside another battery would double the
   packing cost and report the same fact twice.
6. **The `null` fixture is reused for both C3 and C9.** Constructing a
   *same-task* null-reward episode would require a fixture fault that does not
   move the subject bytes, and no such fault exists in this substrate — a fixture
   error is a statement about the subject. Comparing the null bundle with itself
   states the law without inventing a mechanism.

---

## What was not built

Per the ruling, untouched: `eval-…`, `agent-…`, ORS, `trvs serve --ors/--mcp`,
MCP, the REPL. `canonical v1` was not loosened; if git-style diffs are supported
later that will be a named normalizer, not a quiet relaxation.

Next in the ruled order: serial batch, then `bundle-…` distribution identity,
then serve/ORS.
