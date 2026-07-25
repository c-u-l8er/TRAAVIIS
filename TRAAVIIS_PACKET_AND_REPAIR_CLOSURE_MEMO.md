# Packet and Repository Release Closure, and the First Genuine Repair Task

**Status:** shipped · **Battery:** 358 green, 0 failed, 0 skipped · **Date:** 2026-07-24

This memo answers the second GPT-5.6 ruling. That ruling accepted the previous
slice at the code-contract level but found the *release* contract still inexact,
authorized an eight-item **Packet and Repository Release Closure**, and then
directed the work at **the first genuine repair task**.

Both are delivered. The eight items are below in the ruled order, then the repair
task, then the defect the repair task uncovered in production code, then the
decisions taken autonomously that a reviewer may want to overturn.

| run | passed | skipped | failed |
| --- | --- | --- | --- |
| working tree, engine on-path | 358 | 0 | 0 |
| clean extraction, engine on-path | 358 | 0 | 0 |
| clean extraction, engine absent | 292 | 66 | 0 |

All three come from `tools/run_battery.py`. The bottom two are produced by
`tools/accept_packet.py` gates G6 and G5, from an extraction of the delivered
archive, not from this working tree.

> **Counts pinned to this slice.** These are the totals *this* memo shipped and
> they are not updated as the tree grows — a memo that tracks the tree stops
> being a record of what was delivered. The current totals are in
> `TRAAVIIS_WIRING_AND_COMPARE_CLOSURE_MEMO.md`.

**Packet:** `TRAAVIIS_PACKET_AND_REPAIR_CLOSURE.zip`
**Gate:** `ACCEPTED` — G1 through G7, from a clean extraction.

This memo deliberately does **not** quote the packet's SHA-256. The memo ships
inside the packet, so any digest written here changes the archive that the digest
describes; there is no fixed point to quote. The authoritative digest is the one
`tools/build_packet.py` prints on build and `tools/accept_packet.py` re-derives
under G7 from two independent extractors. Per-file digests are in
`PACKET_MANIFEST.json`, which *can* live inside the archive because it excludes
itself.

---

## The eight ruled items

### 1. Canonical executable-mode list

`tools/build_packet.py` no longer reads the executable bit off the working tree.
It declares one:

```python
EXECUTABLE_PATHS = {
    "legacy/node-harness/bin/traaviis.mjs",
    "test/fixtures/fake-claude.sh",
}
mode = MODE_EXECUTABLE if rel in EXECUTABLE_PATHS else MODE_REGULAR
info.create_system = CREATE_SYSTEM_UNIX          # 3
info.external_attr = mode << 16
```

The ruling identified the mechanism exactly. `os.access(src, os.X_OK)` is a
property of the filesystem the source happens to be sitting on, not of the
repository, and Python's `zipfile.extractall` does not restore executable bits
while `unzip` does. So building, extracting with Python, and rebuilding returned
two members as `0644` that had gone in `0755` — a different archive hash from
identical content. Declaring the set in source removes the host from the
identity. `_check_executable_set()` refuses to build if the tree contains an
executable file that is not declared, so the list cannot silently rot.

### 2. In-archive packet manifest

Every packet now carries `PACKET_MANIFEST.json`:

```json
{"manifest_version": "traaviis.packet-manifest.v1",
 "entry_count": "<payload-count>",
 "entries": [{"path": "...", "sha256": "...", "mode": "0644"}]}
```

The count is deliberately symbolic here. An earlier draft of this memo wrote a
literal `103` that was already stale by the time the packet shipped — in a slice
whose whole subject is hand-count drift. The authoritative number is in the
delivered `PACKET_MANIFEST.json` and is checked by G1 (`entry_count` agrees with
`len(entries)`) and G2 (the manifest agrees with the archive in both directions).

`PACKET_MANIFEST.json` is in `SKIP_NAMES`, which is load-bearing rather than
tidy: an extraction leaves the manifest on disk, so without that entry a rebuild
would ship the previous manifest as payload *and* write a fresh one, and the
archive would not be a fixed point of its own builder.

### 3. `tools/accept_packet.py`, gates G1–G7

| gate | what it refuses to accept |
| --- | --- |
| G1 | manifest missing, mis-versioned, unsorted, non-unique, or with a malformed digest or mode |
| G2 | any archive member absent from the manifest, any manifest entry absent from the archive, any digest that does not re-verify |
| G3 | `create_system != 3`, a timestamp other than the fixed one, an archived mode disagreeing with the manifest, members out of order |
| G4 | a required member missing, anything under `legacy/`, `dist/`, `old_scrap/`, `.git/`, `node_modules/`, any `.zip`/`.pyc`/`.pyo`, any path escaping the tree, fewer than 20 test files |
| G5 | with the engine absent: any failure, any crash, **or zero skips** |
| G6 | with the engine present: any failure **or any skip** |
| G7 | a rebuild from a `zipfile` extraction and from an `unzip` extraction that disagree with each other or with the delivered hash |

G5's zero-skip clause is the one worth naming. A battery that reports no skips
with no engine on-path has not proved the engine is optional — it has proved the
engine-dependent laws did not notice it was gone.

### 4. The 335/340 drift

Corrected, and corrected at the root rather than in the prose. The cause was that
the battery had no runner: twenty-two self-running files, two mutually
incompatible summary dialects (`"%d passed, %d skipped, %d failed"` and
`"{n}/{m} passed"`), and a total added up by hand. My own hand count had
`test_exact_closure.py` at 20 when it is 26.

`tools/run_battery.py` counts the per-test `PASS`/`FAIL`/`SKIP` lines every file
already emits, so it is dialect-independent, and it distinguishes a crashed file
from a failing assertion:

```python
crashed = proc.returncode != 0 and tally["FAIL"] == 0
```

The prior memo now carries an erratum and pins its numbers to its own slice
instead of tracking the tree, since a memo that silently follows the code is how
the drift arose.

### 5. Legacy Node harness quarantined

Moved to `legacy/node-harness/` and excluded from the packet by `SKIP_DIRS`,
enforced by G4. Three reasons, all documented in `legacy/node-harness/README.md`:
the package name collided with the `traaviis` console script in `pyproject.toml`;
`npm test` was red from a clean extraction because of a hard-coded absolute path
to a sibling repository; and the description no longer matched what TRAAVIIS is.

`test/fixtures/fake-claude.sh` is needed by both batteries. It stays where the
Python battery owns it and the quarantined test reaches up for it, so the
dependency points legacy → live and never the reverse.

### 6. `EvaluationV1.aggregation_profile`

```json
{"profile": "traaviis.aggregation.mean-over-scored.v1",
 "statistic": "arithmetic_mean", "population": "scored_episodes",
 "unscored_policy": "excluded", "scored": 3, "unscored": 1}
```

The report now states the denominator it used. Laws E14 and E15 pin that an
unscored episode is excluded rather than imputed as zero — the difference is
invisible in a mean but is the whole difference between "we did not score this"
and "this scored nothing".

### 7. `EvaluationV1.runtime_context`

```json
{"registry_version": "...", "wiring": "registry",
 "verifiers_available": [...], "verifier_versions": {...}}
```

Attests the registry that actually ran. `wiring` is `"registry"` or
`"caller_supplied"`, so a report cannot quietly present injected verifiers as the
default set. Per the ruling, **no free-form notes field**: registry `notes` are
deliberately not copied through.

### 8. Cross-extractor deterministic packet identity

Proved at the root and not merely through the gate. Extracting with `zipfile`
*without* applying the manifest modes — so `fake-claude.sh` lands as
`-rw-r--r--`, the exact case that used to break — and rebuilding reproduces the
delivered hash. G7 then does it from both extractors on every acceptance run.

---

## The first genuine repair task

`residency-repair`, a second template on the `residency.repository.v1` substrate.

```
spec/contract.md   requires the module to return 2
src/mod.py         returns 1
target test        baseline exits 1  ->  patched exits 0
health control     baseline exits 0  ->  patched exits 0
```

This is the distinction the ruling was after. Every earlier Residency fixture
answers *"did the agent produce an admissible patch?"*. `evidence-residency`
seeds `return 1` and accepts either `return 1` or `return 2`, so its acceptance
test cannot fail on the subject it ships with — correctly reclassified as a
conformance fixture. This one answers *"did the agent fix the bug?"*, and the
difference is visible in the artifacts rather than only in the prose.

Nine laws in `test/test_repair_task.py`, written mostly against the ways the
shape can be faked:

| law | what it refuses |
| --- | --- |
| R1 | a subject that already satisfies its own spec — then there is nothing to repair |
| R2 | a plan that does not declare the target red on the baseline |
| R3 | a *claim* that the target is red; it runs the declared command against the seeded bytes and checks |
| R4 | anything less than reward 1.0 across all five signals for the real repair |
| R5 | an episode that does not reopen `closed` from its bundle |
| R6 | a fixture whose baseline does not reproduce its own defect being blamed on the agent |
| R7 | a candidate that satisfies the target by destroying what made it meaningful |
| R8 | an admissible, correctly-cited patch that does not actually fix the defect |
| R9 | the template being a renamed copy of the conformance one |

R6 is the phase asymmetry on the case that motivates it: seed the subject already
fixed while the plan still declares the target red, and the baseline contradicts
the task. That is a statement about the *fixture*, so it must be `error` with
`reward: None`, never `fail`.

R7 and R8 are the two ways a "repair" can be admissible and still not be one.
`nofix` patches `return 1` → `return 3`: it applies cleanly, cites the contract
verbatim, and preserves the frozen world's sealed identity. Under
`evidence-residency` that scores full marks. Here the tests cap holds it at 0.4.
`gutspec` genuinely satisfies the target — it really does leave `return 2` on
disk — and is stopped only by the health control.

---

## Two defects found on the way

### The sealed environment is not a channel

The stub agent originally selected its mode from `TRAAVIIS_REPAIR_MODE` in the
host environment. All three modes produced a byte-identical `episode_id`. That is
`runner._seal_env` working as designed: the child environment is built solely
from the task's `agent_run_policy.environment` and the host's is never inherited
(§10a). A fixture cannot smuggle configuration through `os.environ`.

The mode now rides in the agent's argv, which is also the more honest model:
these are three candidate agents answering **one** task, so the task bytes — and
therefore `task-…` — must be identical across all three. R7 now asserts exactly
that (`bad["task_id"] == good["task_id"]`, distinct `episode_id`s), so the
comparison is between two candidates and not two different tasks.

### Multi-file unified diffs did not apply — a production defect

`gutspec` needs to patch two files. It could not, and the failure was not in the
fixture.

`traaviis/patchapply.py` drove its hunk-body loop off each line's **first
character**:

```python
while i < n and lines[i] and lines[i][0] in " -+\\":
```

The next file's section header is `--- a/spec/contract.md` / `+++ b/...`, which
by leading character alone *are* a removal and an addition. The applier swallowed
the following header into the previous hunk and then reported a removed-line
mismatch in the previous file. Multi-file diffs were systematically
unrepresentable — while the module docstring already contemplated multiple
sections ("A file may appear in at most one section").

The counts, not the leading character, terminate a hunk in unified diff. That is
now what the applier does, which dissolves the ambiguity at the root. Because
terminating on counts would let a body longer than its declared counts be
silently skipped by the outer scan, a guard keeps that inadmissible:

```python
if i < n and lines[i] and not lines[i].startswith("--- "):
    raise PatchError(...)
```

Four regression laws in `test/test_patchapply.py`, including the converse — a
hunk that *deletes* a line whose text happens to look like a diff header must
still work, or the fix would have traded one ambiguity for another.

This was reachable from the CLI by any agent emitting a two-file patch, so it is
worth flagging as more than a test-support fix.

---

## Decisions taken autonomously

Flagged for overturning.

1. **The repair mode is an agent argument, not task configuration.** The
   alternative — putting it in `agent_run_policy.environment` — would move
   `task-…` per candidate and make R7's comparison meaningless. I judged one task
   with three candidate agents to be the correct model.
2. **`patchapply` was fixed rather than the fixture reduced to one file.** A
   single-file `gutspec` cannot express "satisfy the target while wrecking the
   repository", which is the law R7 exists to state. The defect was real and in
   production code, so I fixed it and added regression laws.
3. **Terminating a hunk on counts tightened admission slightly.** Content between
   a completed hunk and the next section is now rejected outright, where the
   outer scan previously skipped it. This would reject a `diff --git` preamble
   *between* sections. That seemed right for an applier documented as
   "evidence-grade — exact, fully-admitted, no fuzz", but it is a judgement call.
4. **The prior memo's counts were pinned, not updated.** Rewriting 340 to 358
   would make the memo track the tree, which is the drift failure mode. It now
   carries a note pointing here.
5. **`_ALL_PASS` is used from the test.** R3 needs to ask "is this test red by the
   default rule?", which means judging a command with its phase expectations
   stripped. It reaches for a private name to do so.
   *Superseded:* the reviewer suggested a public predicate instead.
   `substrate_verifiers.command_set_passed(state)` now exists and R3 uses it. See
   `TRAAVIIS_WIRING_AND_COMPARE_CLOSURE_MEMO.md`.

---

## What was not built

Per the ruling, untouched: `eval-…`, `agent-…`, ORS, `trvs serve --ors/--mcp`,
MCP, the REPL.

Next in the ruled order: `trvs compare`, then serial batch, then `bundle-…`
distribution identity, then serve/ORS.
