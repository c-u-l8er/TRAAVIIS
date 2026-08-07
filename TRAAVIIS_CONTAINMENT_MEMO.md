# 9E — background process and workspace exact closure

> **Correction, 9F.** This memo shipped a false claim — *"a cgroup v2 membership
> cannot be left"* — and the code asserted `enforced: true` on the strength of
> it. Both are retracted; see §1 and `TRAAVIIS_CONTAINMENT_CAPABILITY_MEMO.md`.
> What 9E established is an **observed kill boundary**, now named
> `traaviis.cgroup-kill-observed.v1`. It did not establish adversarial
> containment, and the reproduced escape is in the 9F-A record.

**Status.** Implemented and measured on this box, 2026-08-05. Every number below
came from a command in this tree today. Where something was not measured, or
could not be, the line says so.

**What this answers.** The 9E ruling: the eighth erasure route (candidate work
that survives the run), unbounded workspace enumeration, the `lstat`→`open`
race, the symlink ruling, the attribution table, and output-overflow violations.

Two things in here are worth a reviewer's attention before the rest: a **cost**
this closure imposes (§6), and a **consequence** for hosts without cgroup v2
(§5) that changes what a green battery means.

---

## 1. The eighth route, and why the mechanism had to change rather than the timing

The ruling identified two defects, and they need different fixes.

**A successful exit cleaned nothing up.** 9D killed the process group on a
timeout and on an output overflow, and on nothing else. A candidate that spawns
a worker and exits 0 got a clean run *and* left the worker running. That half is
a timing bug — kill on every path, not two.

**`setsid()` escapes the group entirely.** This half is not a timing bug, and
fixing the first would have left it wide open. A process may leave its process
group; a signal addressed to the old group never reaches it. So "kill the group
unconditionally" would have closed the first and not the second.

**A process cannot remove itself from its cgroup**, so a `setsid` descendant is
still a member and `cgroup.kill` still reaches it. That is the property this
mechanism rests on, and it is narrower than the claim 9E made.

**RETRACTED (9F).** 9E said *"a cgroup v2 membership cannot be left"*. That is
false and was reproduced on the host that shipped it: a candidate running as the
same uid writes its pid to the **parent's** `cgroup.procs` and is out. The
kernel's rule is about the delegation boundary, not about any child cgroup ---
*a delegatee cannot move processes outside its delegated subtree; within it, the
delegatee may reorganize processes and create sub-cgroups as permitted.* A
same-uid candidate is inside the delegated subtree with us.

What remains true: `cgroup.kill` is transitive over sessions, process groups and
reparenting to init, and `cgroup.events`' `populated` flag is the kernel
answering "is anything still alive **in here**" — rather than a `/proc` walk by
parent pid, which reparenting makes useless. That closes the eighth route. It
does not close the ninth.

Measured through the real agent runner, all three shapes:

```
bgchild  exit=0  surviving=0  enforced=True  descendant_alive=False
setsid   exit=0  surviving=0  enforced=True  descendant_alive=False
daemon   exit=0  surviving=0  enforced=True  descendant_alive=False
```

`daemon` is the double fork: reparented to init, no parent-child path from
anything the evaluator knows to the survivor.

### Getting the child into the cgroup before it can fork

Writing the pid from the parent after `Popen` returns loses the race by
construction — the child may fork first. `preexec_fn` runs between `fork` and
`exec` in a process that may hold locks taken by other threads, and `mcp_server`
really does serve requests on threads; that is the identical hazard
`forge_adapter` cites when it refuses `multiprocessing`'s `fork` start method.

So the child is a small inline bootstrap (`containment.GUARD_SOURCE`) that joins
the cgroup and then `execv`s the real command. Two details are load-bearing:

- **`-c`, not `-m`.** `-m traaviis.spawnguard` would need the package on the
  child's path, and the agent's environment is a *sealed map* — adding
  `PYTHONPATH` would change `environment_keys`, which is inside the canonical
  trace event, which would move every `trace-…` ever minted.
- **A close-on-exec status pipe.** Inserting a bootstrap between `Popen` and the
  command would otherwise destroy the contract that a nonexistent `argv[0]`
  raises `FileNotFoundError` — it would become a successful launch of a program
  that exits 127. EOF on the pipe *is* the report that the exec succeeded; an
  errno arrives if it did not. `OSError(ENOENT, …)` **is** a `FileNotFoundError`,
  so callers catch exactly what they caught before.

### A duplicated guard made one of the existing proofs unsound

The most useful thing this slice turned up, and it was found by a law going red
for the wrong reason.

`test_evalone::W2` proves the lowering worker's kill reaches a grandchild, with
a non-vacuity probe that deletes the kill in an isolated copy and requires the
grandchild to survive. After 9E it failed — reporting *"the grandchild died even
with the group kill deleted"*. The obvious reading is that the cgroup was doing
the killing and the probe needed retargeting. That reading was wrong.

`kill_process_group` existed **twice**: written out in `execlimits`, and written
out again inside `Containment.kill_all`. The probe deleted one; the other was
still doing the group kill. So the law was not measuring a boundary — it was
measuring its own failure to switch the boundary off, and it had been reporting
success on that basis.

That is worse than an ordinary duplicate. A second copy of a *guard* makes every
deletion-based proof about that guard unsound, silently, in the direction that
says everything is fine — and deletion-based proofs are this repository's main
instrument against vacuous laws.

There is now one implementation, in `containment`, with `execlimits` re-exporting
the name. **X21** forbids a second: exactly one `os.killpg` call in the package,
located on the parse tree, and every other name for it must resolve to the same
function object.

### The boundary was leaking the thing it is made of

Found by looking at the cgroup directory, not by a law going red — and nothing
in the suite would have caught it later either.

`rmdir` immediately after the drain can fail with `EBUSY`: the kernel reports
`populated 0` a moment before it will remove the directory. A single attempt
therefore leaves an **empty cgroup** behind. Seven had accumulated on this box in
an afternoon's work:

```
traaviis-run-1138530-1   procs=0  populated 0
traaviis-run-1146154-1   procs=0  populated 0
…five more…
traaviis-run-1284660-1   procs=1  populated 1   (a live run)
```

Individually harmless, never self-clearing, created thousands of times by a
running evaluator. That is the resource-exhaustion class this whole slice exists
to close, one layer beneath the runs it closes it for.

`close()` now retries briefly, and `open_containment` sweeps directories whose
name carries a pid that no longer exists *and* which the kernel reports
unpopulated. **Y21** pins both, and the assertion that matters is the sweep's
safety rather than its effect: a live run's cgroup is named after a live pid, so
it can never be swept out from under a concurrent evaluation. The law checks
that directly, and uses `pid_max + 1` to stand in for a crashed evaluator, since
that branch cannot be reached by crashing the battery on purpose.

### One concurrency defect found and fixed on the way

The first version drew cgroup directory names from `_SEQUENCE[0] += 1`. That is
a read-modify-write, and the interpreter may switch threads inside it — so two
concurrent runs could draw the same number, `mkdir` would fail, and the fallback
would **silently downgrade a contained run to best-effort**. A silent downgrade
of a safety boundary is worse than no boundary, because the receipt still says
the run was watched. It is now `itertools.count` (a single C call) plus a retry.
Verified: 24 concurrent threads, 24 distinct cgroups, all enforced.

---

## 2. Enumeration is bounded per *entry*, not per file

The ruling's example is exact: a million empty directories crosses no file-count
bound and still costs unbounded time and memory, because the cost of collection
is per entry. Five new bounds, all declared in `LIMITS`:

```
max_workspace_entries       20000     every entry, of every type
max_workspace_directories    2000
max_workspace_depth            32
max_workspace_path_bytes  1048576     the sum of all relative names
max_single_path_bytes        4096     one relative name
```

Every one refuses at the first proof. Measured (Y6): a 4 000-directory tree with
a bound of 100 is refused in a small fraction of the time it took to *build*,
which is the difference between bounding enumeration and walking it first.

---

## 3. The check/use race, closed by descriptors rather than by care

9D did `lstat(path)` → verify regular → `open(path)`: three operations on a
*name*. Now:

- traversal is `os.fwalk`, which yields a **directory descriptor** per level;
- every file is `os.open(name, O_RDONLY|O_NOFOLLOW|O_NONBLOCK, dir_fd=…)`;
- the type check is `fstat` **on the descriptor**, so what is verified is the
  object now held open — a rename or replace changes what the *name* refers to,
  never what an open descriptor refers to;
- `read_file_bounded` resolves `result.json` / `candidate.patch` one component
  at a time with `O_NOFOLLOW|O_DIRECTORY`, so no directory above the file can be
  swapped either.

`O_NONBLOCK` is why reaching a FIFO costs nothing: the open returns instead of
waiting for a writer. `O_NOFOLLOW` is why a symlink swapped in cannot redirect
the read out of the workspace — the kernel refuses to traverse it.

Y10 and Y11 state the property without simulating a race, deliberately: whatever
the name refers to *at the moment of the open* is what the open must survive, and
that is what is exercised. A sampled race would be a weaker claim and a flakier
test.

---

## 4. Symlinks: refused. The ruling is right and the 9D reasoning was wrong.

9D skipped them and recorded the deviation. The ruling's argument settles it:
snapshot materialization cannot express a symlink, the workspace begins from that
sealed materialization, therefore any symlink present afterwards is
candidate-created. Skipping means the trace does not describe the complete
candidate-created filesystem state — incomplete in the direction that pays the
candidate, which is the exact failure `_evidence_name` was written to avoid.

The denial-of-service worry I raised does not apply to a v1 subject that could
not have contained one. Frozen: **regular files and directories only**; a
candidate-created symlink is a resource-policy violation, episode invalid,
reward 0.

---

## 5. Attribution, and what it costs a host with no cgroup v2

The table is implemented as ruled. The two that changed verdicts:

| event | was | now |
| --- | --- | --- |
| agent stdout/stderr over the cap | `error` / `null` | invalid / `0` |
| patched WRL over the source-size profile | `error` / `null` | identity `fail` |
| baseline test command over the cap | — | verifier `error` (invalid fixture) |
| patched test command over the cap | — | tests `fail` |
| Forge wall-clock timeout | `error` / `null` | **unchanged** |

`ForgeSourceTooLarge` stopped being a `ForgeUnavailable` subclass. That is the
substantive part: left as one, the existing `except ForgeUnavailable` handlers
would have kept turning it back into `error` whatever the docstring said.

The output-overflow change touches `evalone._finish_episode` **and** the replay
in `episode_bundle`, mirrored exactly — an overflowed run reports no exit code,
so `exit_code is None` no longer means "timed out" on its own, and the persisted
truncation flags are what tell the two apart.

### The consequence, stated plainly

A run that cannot be contained records `/resource-limit.v1:containment_unavailable`,
which makes the episode **invalid**. That is the ruling's "refuse the hostile-run
profile" option, taken.

So **on a host with no delegated cgroup v2, every episode is invalid.** That is
deliberate, and it has a review-process consequence I want in front of you rather
than discovered: the containment laws Y1–Y5, Y12 and Y13 **skip** on such a host
rather than pass, because a law about what a boundary prevents passes trivially
where there is no boundary — and a green battery would then read as evidence that
the eighth route is closed. G6 forbids skips, so such a host is told it cannot
certify the packet, with the skip naming what went unverified. Y20 covers the
other half on every host, structurally: a missing mechanism must be *recorded*.

If you would rather the packet be certifiable on a host without cgroup
delegation, that is the "label it explicitly as best-effort" option instead, and
it is a one-line change to what `containment_unavailable` produces. I did not
take it, because it lets an uncontained evaluation look like a contained one.

---

## 6. The cost, measured

The bootstrap is an extra interpreter start on **every** spawn — the agent, every
test command, every Forge worker:

```
contained run_bounded   44 ms
plain subprocess.run    13 ms
overhead per spawn      31 ms
```

About 18 ms of that is the interpreter; the rest is the cgroup create/kill/drain.
`-S`/`-I` flags recover ~2 ms and were not taken for that. The drain poll
escalates from 0.2 ms rather than sitting at a flat 5 ms, which matters across a
battery of thousands of commands.

This is a real regression in suite wall-clock and I would rather state it than
have it turn up as "the battery got slower". The alternatives all trade it for a
weaker boundary: `preexec_fn` is unsafe with threads, and moving the pid from the
parent loses the fork race.

---

## 7. Laws

`test/test_containment.py` — **20 passed, 0 skipped, 0 failed** on this host.
The ruling's seventeen, plus Y18 (the attribution rule as an executable table,
so a future refusal has to be classified rather than inherit whichever handler it
lands in — which is how an oversized source came to be `error` in the first
place), Y19 and Y20.

`test/test_execlimits.py` — 20/0/0. **X19 was rewritten**, because its subject
stopped being the mechanism: it deleted `killpg` and watched a cooperative
grandchild die, which the ruling showed was proving something about the wrong
boundary. It now forces the isolated copy down to `traaviis.process-group.v1`
and requires the **setsid** escape to come back and `enforced: false` to be
reported — reproducing the ruling's finding as the non-vacuity proof.

---

## 8. Where the containment record lives, and where it does not

The ruling's shape is implemented verbatim, under the ruling's own key name:

```json
"process_containment": {
  "profile": "traaviis.cgroup-v2.v1",
  "enforced": true,
  "remaining_processes": 0
}
```

It is on the `RunResult` (and on `BoundedRun` beneath it), so every caller that
collects evidence has it. The ORS submission adapter carries it too, reporting
`traaviis.uncontained.v1` with `enforced: false` — which for that profile is not
a weaker guarantee but the honest label for an inapplicable question, the same
reading `execfacts` already takes for its sandbox fields.

**It is not in `execution_facts`, and that is a decision rather than an
oversight.** `execution_facts` is inside the receipt, which is inside
`episode-…`, so a new top-level key there moves **every episode id ever
minted** — for every profile, on every host, including the shipped examples.
`execfacts`'s own note draws the line: adding a *key to `RUNNER_PROFILES`* is
additive and moves nothing, adding a *field to an existing profile* moves
everything.

So the enforcement is recorded where it is checkable and the ids stay where they
are. If the ruling wants it attested inside `episode-…`, that is a deliberate
identity migration and should be ruled as one — it is not something to do as a
side effect of a resource fix.

## 9. Item 9: the verdict embedded in the packet

`accept_packet.py --report` now emits the verdict as JSON, classifying each gate
by **subject**:

- **content gates** (G5, G6) are functions of the extracted tree. Adding a JSON
  report does not change what the battery does, so they carry unchanged into a
  rebuild. These are what the ruling's complaint was about: the 9D packet
  shipped a memo that ended while G5/G6 were still running.
- **archive gates** (G1–G4, G7) hash, inspect and rebuild the delivered `.zip`.
  A verdict inside an archive cannot describe the archive containing it, because
  adding the verdict changes the bytes. The record names the packet it judged
  and says so; recomputing them means running the script on the file in hand,
  which was always the only way they could be checked.

### Pass 1, measured

```
traaviis-9e-pre.zip
e90b2ab1ce44e1cbb7e3adca6ec3c6ccfea7babb7fbc0164d75bd5d3a4493388

ok  G1  manifest well formed              161 entries
ok  G2  manifest agrees with archive      161 members hashed, all match
ok  G3  archive metadata canonical        unix modes, fixed timestamps, sorted
ok  G4  contents complete and clean       161 members, 35 test files
ok  G5  battery, engine absent            622 passed, 169 skipped, 0 failed
ok  G6  battery, engine present           791 passed,   0 skipped, 0 failed
ok  G7  rebuild is extractor independent  reproduced from both extractors

ACCEPTED
```

Against the 9D packet: G5 600 → **622**, G6 768 → **791**, skips unchanged at
169 engine-absent and 0 engine-present.

### Pass 2 — the delivered packet

```
traaviis-9e-containment.zip
05d65f3db8b2e9c1618adb2625c92c81eb671a6ec2654ad54d2f1cc368ed079f

ok  G1  manifest well formed              162 entries
ok  G2  manifest agrees with archive      162 members hashed, all match
ok  G3  archive metadata canonical        unix modes, fixed timestamps, sorted
ok  G4  contents complete and clean       162 members, 35 test files
ok  G5  battery, engine absent            622 passed, 169 skipped, 0 failed
ok  G6  battery, engine present           791 passed,   0 skipped, 0 failed
ok  G7  rebuild is extractor independent  reproduced from both extractors

ACCEPTED
```

**The claim the embedding rests on, earned rather than asserted.** G5 and G6 are
identical across the two passes — `622 / 169 / 0` and `791 / 0 / 0` both times.
Only the archive gates moved, and they moved exactly as much as adding one file
should: 161 → 162 members, a new hash, G7 still reproducing it from both
extractors. So the verdict this packet carries describes the same content tree
the packet delivers, and the only thing it cannot describe is its own bytes —
which is stated in the record rather than glossed.

One run was **stopped rather than reported**: the gates were started on a build
made before `containment` was renamed to `process_containment`, and a verdict on
a tree that is not the delivered one is not evidence about the delivered one.
Killing it and starting over costs forty minutes and is the only honest option;
recording it as "the 9E acceptance" would have been the same mistake as shipping
a memo that ends mid-run.
