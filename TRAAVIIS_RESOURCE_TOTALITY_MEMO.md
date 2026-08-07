# 9D — resource totality, the language-neutral number profile, and the twelve

**Status.** Implemented and measured on this box, 2026-08-05. Every number below
was produced by a command in this tree today; where something was *not*
measured, the line says so.

**What this answers.** The ruling that accepted 9A/9B/9C and 10 held resource
totality open on four counts, ordered two tracks in parallel, and required the
twelve structural-source exceptions closed before item 11. This memo is the
report on all of it, plus item 7 (`COMMIT_PROVENANCE.json` is not proof yet),
which was cheap and is discharged.

Two things in here are **not** closures and are flagged as such: one semantic
question I declined to decide unilaterally (§6), and three defects I found in
Law B's own checker while closing the twelve (§5).

---

## 1. The seventh erasure route, and the three more in the same seam

The ruling's diagnosis was exact, including that it is broader than one
function. All four forms are closed by one module.

```
traaviis/execlimits.py      traaviis.execution-limits.v1
```

### The FIFO

`test/fixtures/stub_agent.py` gained a `fifo` mode: `os.mkfifo("output.pipe")`,
then `sys.exit(0)`. Before this change `runner._scan` walked every non-symlink
filename and did `open(abspath, "rb").read()` with no check that the entry was a
regular file, so that call **did not return** — the agent was already gone, so
no writer would ever arrive. No exception, no receipt, no trace, no episode.

Now: `execlimits.scan_tree` `lstat`s every entry and refuses anything that is
not `S_ISREG`, before any `open`. Measured (`test_x1`): the call returns, the
refusal is named and sealed, and the episode is **worse** for the candidate than
the score it was trying to erase — see §2.

### Unbounded files, and bounds applied after the read

| what | before | now |
| --- | --- | --- |
| workspace file count | unbounded | 10 000 |
| single workspace file | unbounded, read whole | 64 MiB, streamed in 1 MiB chunks |
| total workspace | unbounded | 256 MiB |
| `result.json` | `fh.read()`, *then* the 8 MiB JSON bound | 8 MiB, `lstat` + `read(max+1)` |
| `candidate.patch` | `fh.read()`, then a UTF-8 decode | 8 MiB, same |
| `workspace_after` | **a second whole copy of the post-run workspace, decoded** | removed |

`boundedjson`'s own docstring had already named the missing piece — *"the real
closure for size is a declared byte bound on the result file (a fact about the
bytes, identical on every host)"*. This is that bound.

`workspace_after` deserves its own line because nothing consumed it. `evalone`
never read it; no receipt, trace, bundle or replay derived anything from it. It
existed for one battery assertion, which wanted the bytes of `result.json` — and
those are `result_bytes`, which `run_agent` already has, already bounds, and
already hashes into `result_file_digest`. The field is gone; `test_runner` now
checks the bytes that were *sealed*, which is the better subject anyway.

### Output capped after capture, and a timeout that is not a process-tree bound

`subprocess.run(stdout=PIPE, timeout=…)` gets two things wrong that cannot be
fixed by wrapping it, and the ruling named both. It buffers everything and
applies `max_output_bytes` afterwards; and its timeout kills the direct child,
so a grandchild that inherited stdout keeps the pipe open and the collection
goes on waiting.

`execlimits.run_bounded` replaces it on **every** path that touches
candidate-influenced input:

```
runner.run_agent                    agent
substrate_verifiers.run_command_set tests verifier
forge_adapter._lower_in_worker      identity verifier's worker
```

Session leader, reader threads that cap memory *while still draining the pipe*,
a supervisor that kills the whole **group** on deadline or overflow, bounded
reap. `test_x7` measures the retained bytes never exceeding the cap against a
child writing forever; `test_x8` spawns a grandchild holding stdout and asks the
OS whether it survived.

The `VERIFIER_ISOLATION_POLICY` row that said `tests: subprocess_timeout` now
says `bounded_process_group`, because the ruling was right that the old row said
only that a clock existed.

### One deviation from the letter of the ruling, stated rather than buried

The ruling's implementation list says *reject FIFO/socket/device/symlink*. FIFO,
socket, character device and block device are **rejected**. A **symlink is
skipped**, which is the rule `_scan` already had and which I kept deliberately:
nothing is ever opened through one (that is what the type check on the resolved
entry is for), and refusing them would turn a workspace containing an ordinary
symlinked directory into a refused episode — trading an erasure route for a
denial of service against honest candidates. The declared profile still reads
`regular_files_only: true`, because only regular files are ever *read*.

If the ruling intends a symlink to be a refusal rather than an omission, say so
and it is a two-line change; I did not want to make a candidate-facing
availability decision by reading a list literally.

### Two things I got wrong on the way, since both are instructive

**The cleanup hung on the one path the kill exists for.** The first version of
`run_bounded` closed the pipes from the supervisor after the kill.
`io.BufferedReader.close()` takes the buffer's lock, and the reader thread holds
that lock while blocked in `read()` — so when a grandchild survived and no EOF
was coming, the *cleanup* hung past 60 seconds having already decided the
verdict. Each reader now closes its own stream. Caught by **X19**, the
non-vacuity probe, which is the argument for writing those.

**Containment absorbed something it should not have.** `run_bounded` catches the
`OSError` from a failed spawn and reports it, which is right for the tests
verifier — a record saying `FileNotFoundError` is evidence, and a host path in
an exception message must not enter a canonical record. It is wrong for
`run_agent`, whose contract is that an agent with a nonexistent `argv[0]`
**raises**: `evalsplit._run_episode` catches `OSError` so one caller's typo costs
one task instead of abandoning the split and, once several candidates share a
split, every remaining candidate. Absorbed, the candidate got a complete
`RunResult` describing a process that never existed and was then compared
against candidates that really ran. `test_batch::B14` caught it, and `X20` now
states it where the boundary lives — `run_bounded` reports, `run_agent`
re-raises — so the next widening of the containment sees why this case is
outside it.

### Determinism

`run_bounded` reports `exit_code: None` for **any** run that emitted more than
the profile retains, whether or not the supervisor noticed before the child
exited. The overflow flag is set by the reader threads on the bytes themselves,
so it is a fact about the output rather than about who won a scheduling race. A
version that reported the real exit code when the kill lost the race would have
minted two different traces for one input.

---

## 2. A refusal is a persisted outcome, and it costs the candidate

The ruling: *resource-policy violations must become a deterministic persisted
outcome, never a host OOM or hang.* Two decisions implement that.

**Every refusal is recorded in `policy_violations`,** under the reserved prefix
`/resource-limit.v1:`. A leading `/` is unreachable for an
`os.path.relpath`-derived name, so a candidate cannot forge one by naming a
file. This is not filing convenience: `evalone` already reads a non-empty
`policy_violations` as **tampered** — reward 0, `validity: invalid` — live and
on replay, through a bundle member that already exists and a trace digest that
already covers it. So a candidate that leaves a FIFO or writes a 20 GiB
`result.json` earns a **zero**, which is strictly worse than the failing score it
was trying to escape. Closing an erasure route is worth nothing unless that
holds.

**The profile name is sealed only where it mattered.** `execution_limits_version`
is an optional member of the canonical trace event: `identity.canonicalize_trace`
copies keys `if k in e`, so a run that stayed inside the bounds canonicalizes to
exactly the bytes it did before this change and **no sealed `trace-…` or
`episode-…` moves**. A run that hit a bound had no id to move — before
`execlimits` it hung or died. The same conditional discipline is used for
`stdout_truncated` in the tests verifier's per-command evidence.

---

## 3. The Forge IPC exemptions were misclassified. Agreed, and closed.

The ruling is right and the argument in the old exemption is the tell: it
reasoned from who wrote the **envelope** and not from who wrote the **payload**.
`source` is candidate-modified WRL; the response carries an engine-emitted
diagnostic. Both ends now parse through `boundedjson` under a narrower IPC
sub-profile:

```
max_ipc_source_bytes       1 MiB
max_ipc_request_bytes      2 MiB
max_ipc_response_bytes     256 KiB
max_ipc_diagnostic_bytes   64 KiB     (= boundedjson.MAX_DIAGNOSTIC_BYTES)
```

- Parent: bounds the source *before spawning anything* (`_encode_request`), so an
  oversized source costs one `len` on already-resident bytes and no worker at
  all. `ForgeSourceTooLarge` is a `ForgeUnavailable`, so every existing handler
  already does the right thing — see §6 for the part of this I am not deciding.
- Worker: `sys.stdin.read()` → `stream.read(MAX + 1)`, one bounded parse, and a
  response that is truncated-and-marked rather than partially written.
- Both sites moved from `PARSE_EXEMPTIONS` into `PARSE_SITES` with the honest
  trust classes `candidate-influenced IPC` and `engine-influenced IPC`.
  `PARSE_EXEMPTIONS` is now **empty**.

Law A caught the new parse site the first time it ran, which is the second time
that registry has proved non-decorative.

---

## 4. The oracle is rerunnable now, and it was worth rerunning

The ruling was right that shipping the audit directory made the oracle
inspectable and not executable, and right about each specific defect (the
hard-coded `/tmp/claude-…/scratchpad` import, the user-specific Node path, the
missing `rust.tsv`/`v8.tsv`, `t.rs` printing two values). One relocatable entry
point now exists:

```bash
python3 audit/number-oracle/run_audit.py
```

It locates or accepts `node` and `rustc` (`--node`/`--rustc`, `$NODE`/`$RUSTC`,
`PATH`), regenerates V8's output from the included bit patterns, regenerates
Rust's, derives the minimum round-tripping decimal length **independently** over
exact `fractions.Fraction` arithmetic, picks the closest candidate at that length
with ties-to-even, compares everything, reproduces `oracle.tsv`, and fails on any
drift. No absolute paths (a law checks that, structurally). Exit status
distinguishes drift (1) from a tool being absent (2), because collapsing those
is how "the audit passes" comes to mean "the audit did not run".

Measured today, and again from a copy of the tree extracted at a different path:

```
boundary vectors: 484
independent oracle: derived 484 values by exact rational arithmetic
oracle.tsv:  reproduced, 484/484
traaviis.jcs: agrees on 484/484 boundary vectors
V8:          agrees on 484/484 boundary vectors        (node v25.2.1)
V8 corpus:   regenerated 76926 vectors, identical to the recording
jcs corpus:  agrees with V8 on 76926/76926
Rust k:      agrees on 484/484 shortest lengths        (rustc 1.94.1)
Rust digits: recorded 6 expected divergence(s)

OK: every stage green.
```

The 6 divergences are recorded in `audit/number-oracle/rust_divergence.tsv` and
compared on every later run, so a seventh is drift. They are three values and
their negatives, and one of them is RFC 8785's own Appendix B "Round to even"
row — `43143ff3c1cb0959`, Rust `1424953923781206.3`, ES2019
`1424953923781206.2`. That is the prior memo's claim, independently reproduced
rather than restated.

**The derivation is genuinely a third one.** Production searches `%.*e`; the
battery's reference adopts `repr`'s digits through `Decimal.as_tuple`; the audit
does neither — it rounds the exact rational value of the double to `k` digits
with `ROUND_HALF_EVEN` and tests the round trip, for increasing `k`. Their
agreement is therefore evidence rather than tautology.

### The profile itself

`JCS_IJSON_CLOSED_NUMBER_PROFILE_V1` is implemented in `traaviis/jcs.py` with
ten laws in `test/test_numberprofile.py`. The rule, and every consequence the
ruling names, measured:

```
int(2**54)   reject     float(2**54) reject
int(10**21)  reject     float(1e21)  reject
1e20         reject     1e16         reject
NaN, ±Inf    reject     -0.0         renders as 0
2**53 - 1    admit      0.1, 5e-324  admit
```

The lexical discontinuity is gone: under the old profile `1e20` is refused and
`1e21` admitted, because ECMA-262 step 6 stops rendering positionally at
`n = 21`; under the new one both are refused, because both are integral and both
are above `2**53 - 1`.

**The cutover has not happened, and a law (`N9`) exists to keep it from happening
by accident** — it checks the default of `canonical_bytes(profile=…)`, checks
`identity.SCHEME_RFC8785`, and walks the package for any reference to the new
profile outside `jcs.py`. It is written to be *deleted* at item 13, not weakened.
`N6` proves the two profiles produce byte-identical output for anything both
admit, so the cutover is a narrowing rather than a re-hashing.

---

## 5. The twelve are closed — and Law B's checker had three defects

All twelve registered structural-source violations are now AST-based and none is
flagged. `SOURCE_TEXT_SITES` holds four entries, all `prose`, all reading another
battery's stdout, which really is prose.

```
test_canonical.py   C18  C42  C50  C52
test_kernel.py      K4   K16  K28
test_evalone.py     W6
test_ors.py         O13  O27  O28  O30
```

W6 had to move anyway: its proof was `"subprocess.run(" in src`, which 9D made
false. The other eleven are a pure test-hygiene change, in the ruling's own
words *"do not mix that commit with split-level coverage semantics"* — nothing
about coverage was touched.

**And this is the part worth a reviewer's attention.** Closing them required
three amendments to `_structural_claims` itself, each of which makes the checker
flag *less*, and every one of which was a case where **a law rewritten onto the
AST — the remedy Law B demands — stayed flagged**:

1. `taints` passed the taint straight through `ast.parse`, despite the
   docstring promising since day one that *"handing a tainted value to
   `ast.parse` produces a tree, and claims about that tree are structural and
   are not flagged"*. It was documented and never implemented.
2. `returns_source` classified any helper that *forwarded* a source-fetching
   helper as returning text, without ever looking at what it returned — so
   `def helper(f): return list(_tree(f).body)` read as text.
3. The `for`-loop rule walked every subexpression of the iterable, so
   `for n in ast.walk(ast.parse(getsource(M)))` tainted its loop variable off
   the `getsource` buried three calls down. That is the canonical structural
   idiom, and the one Law B tells people to write.

Together they meant the register could not be emptied by complying with the
rule. That is the ruling's own predicted failure mode — *registered exception →
normalized exception → law stops protecting the suite* — reached from the other
side: a checker that cannot recognise its own remedy makes the register
permanent.

**So the obvious question — did I close the violations, or the checker's eyes —
has its own law.** `J25` plants the original shape of each closed violation
(`str.index` ordering over `getsource`; a substring of a substring; `.lower()`
over a whole module; `source.count("with self._lock:")`) and requires all four
still flagged, and plants the fixed shapes and requires them clean. It passes in
both directions.

---

## 6. One thing I did **not** decide

`ForgeSourceTooLarge` is a `ForgeUnavailable`, so an oversized WRL source makes
the identity verifier `error`, and `error` means `reward = None` — the episode is
**unscored**.

That is candidate-attributable, unlike a missing engine. A candidate that writes
a 2 MiB WRL file gets its identity signal unscored rather than failed, which is
the shape of the erasure routes this whole line of work closes.

I implemented it as `error` anyway, because that is what the existing ruled
contract says: `ForgeTimeout` is equally candidate-triggerable and was ruled
`error` on the ground that *"a worker that dies is never evidence against the
candidate"*. Changing it is a semantic ruling about what an unanswered verifier
means, not a resource fix.

**The same question is older and larger than the Forge worker.** §10a already
says a truncated capture sets `output_truncated` and *"the affected verifier
reports `error` (substrate unavailability), not `fail`"*. So a candidate that
emits 5 MiB of stdout has, by the specification, been able to null its own reward
since before any of this work. 9D bounds the memory; it does not change what the
outcome *means*, and I do not think it should decide that in passing.

Raising it rather than acting on it. If the answer is that a bound the candidate
crossed with its own bytes should be `fail`, that is a small change in three
places and a change to §10a's text.

---

## 7. `COMMIT_PROVENANCE.json` is proof now

The ruling: the recorded fields cannot recompute a commit hash, because a hash
closes over the full message, both identities, both timestamps, encoding and
headers, and the packet ships no Git objects. Both halves are fixed by
`tools/build_provenance.py`, using both routes the ruling offered.

- Every entry carries `raw_base64`, the exact bytes of the commit object. Git's
  id is `sha1(b"commit %d\0" % len(raw) + raw)`, so a reader with this JSON file
  and a SHA-1 implementation — **no Git, no repository** — can recompute every
  hash. `--verify` does that, and also checks that the readable projections
  (`tree`, `parents`) agree with the bytes they were projected from, so the
  readable half cannot drift from the checkable half.
- `provenance/history.bundle` is a real Git bundle. `git clone` it and the tree
  and blob objects the JSON names can be resolved rather than trusted.

Measured:

```
$ python3 tools/build_provenance.py --verify
recomputed 61 commit hashes from the file alone: all agree
bundle present: provenance/history.bundle (2682580 bytes)
```

---

## 8. Measured suite state, and the packet

Two new battery files account for 30 laws: `test/test_execlimits.py` (X1–X20) and
`test/test_numberprofile.py` (N1–N10).

```
$ python3 tools/accept_packet.py dist/traaviis-9d-resource-totality.zip \
      --forge ../TRVM/forge

packet    traaviis-9d-resource-totality.zip
sha256    c1314c7a763d4312fa2d374362cba120a680fa60e717bd6c011fe63f69585f74

ok   G1  manifest well formed               158 entries, traaviis.packet-manifest.v1
ok   G2  manifest agrees with archive       158 members hashed, all match
ok   G3  archive metadata canonical         unix modes, fixed timestamps, sorted
ok   G4  contents complete and clean        158 members, 34 test files, nothing smuggled
ok   G5  battery, engine absent             600 passed, 169 skipped, 0 failed
ok   G6  battery, engine present            769 passed,   0 skipped, 0 failed
ok   G7  rebuild is extractor independent   c1314c7a763d4312 from both extractors

ACCEPTED
```

The prior packet's `569 passed / 169 skipped` was engine-**absent**, and the
ruling correctly noted the complete battery was still running when that packet
was sent. The comparable number here is G5's: **600 / 169 / 0**.

### The first acceptance run was REJECTED, and the gate was right

Worth recording, because the failure was mine and the shape of it is the point.
The build immediately before this one passed G1–G5 and G7 and **failed G6** on
`0 failed, 1 skipped with the engine present`.

The skip was `J15`, reporting *"the exemption register is empty"* — it had
nothing to check, because both Forge IPC entries had moved into `PARSE_SITES`.
I had written that off as an honest skip. It was not, twice over. A skip means
*"this tree cannot test this"*, and the truth was *"there is nothing registered
to test"* — a different fact reported with the same word. And a law that goes
quiet exactly when its subject disappears is a law nobody notices has stopped
working: the **next** exemption would have been judged by a checker that had not
run in months. That is the same normalization the ruling warned about at §6,
arriving through the register's other end.

So `J15` now has a subject when the register is empty: the checker itself. The
judgement is factored into `_catchall_guarded`, and the law plants three
synthetic modules — one under `except Exception`, one under the exact narrow
`(ValueError, UnicodeDecodeError)` clause this whole battery exists because of,
and one where a *comment* claims the handler — and requires it to tell them
apart. A registered exemption, whenever one exists again, is judged by the same
function.

G6's "nothing skips" rule found this. A checklist would not have.

## 9. What is next, in the ruled order

```
[done] Track A  9D execution I/O totality
[done] Track B  language-neutral numeric profile + relocatable oracle
[done]          close the 12 structural-source exceptions
[done]          item 7, provenance
[done]          the complete 7-gate packet acceptance   ACCEPTED
  ->            item 11  split-level coverage aggregation
                item 12  paired 0.55 coverage demonstration
                item 13  v2 cutover        (gated on run_audit.py, now green)
                item 14  [&]code -> TRAAVIIS
```

Resource totality is claimed closed on the ruling's own condition: **the FIFO law
passes** (`test_x1`), and it passes because the scan refuses the file rather than
because the fixture stopped creating one — `X17` deletes the guard in an isolated
copy of the package and the scan hangs again.
