# Residency Submission ORS Profile v1 — memo

**Slice:** `trvs serve --ors`. Give the Episode Kernel its first transport: an
HTTP surface that accepts a candidate finding+patch, scores it against a packed
environment, publishes the evidence, and returns the reward.
**Status:** shipped.

**Battery.** O1–O30: **30 passed / 0 skipped / 0 failed**. Whole tree:
**532 passed / 0 skipped / 0 failed** across 28 files, up from 502/27.

Unblocked by the previous slice. The seventh GPT-5.6 ruling froze the ORS
profile — one tool, the exact key set, the non-executing runner profile, the
`finished` durability rule, admission-before-binding, and laws O1–O30 — and
blocked it on the finalize race, which the linearization slice closed. This
slice implements that ruling and nothing else.

---

## 1. What a transport is allowed to be

The kernel was extracted (§4a) so that a transport could be a **translation
layer** rather than a second implementation. This slice is the first test of
whether that was true, and the answer is legible in the shape of the diff:
`traaviis/ors.py` is 740 lines of *validation and translation*, and it computes
no reward, opens no episode, and writes no bundle. Every one of those is a
kernel call.

The strongest statement of this is O3. The three interactive routes —
`observe`, `step`, `reset` — do not have a transport-level "not implemented"
branch. They call the kernel, the kernel raises `KERNEL_OPERATION_UNSUPPORTED`
because the Residency substrate is one-shot, and the transport maps that to
501 and relays the code verbatim. **The refusal is in the substrate's words,
not the transport's.** A transport that owned the refusal would keep answering
`501` on the day a substrate learned to step.

## 2. One tool, because the substrate has one

`submit_candidate` is the entire tool surface. The banner and `/describe`
advertise exactly that, plus the three operations this substrate refuses, by
name.

There is a real temptation to publish a richer vocabulary and stub the rest,
because catalogs are cheap and clients like options. It is the same temptation
as fabricating an exit code, and it fails the same way: an advertised
capability is a *claim*, and a claim the substrate cannot honour is the kind of
thing that gets discovered by a client's retry loop at 3am rather than by a
law.

## 3. The exact key set is a trust boundary, not a schema nicety

`RemoteSubmissionV1` (`traaviis.remote-submission.v1`) is validated by **exact
key set** — `{submission_version, finding, patch}` — not by "these keys are
required". The difference is the whole point.

Everything else in an `EvaluationRunV1` is *server-determined*: the task, the
environment, the toolchain, the platform, the verifier versions, the reward,
the execution facts. A client that could set any of them could mint an episode
that verifies closed and says something false. So:

- `CLIENT_FORBIDDEN_FIELDS` are refused **by name** (O5), because "unknown
  field" is a bad error message for `reward`;
- any *other* unexpected key is refused too (O6), because the forbidden list is
  a courtesy and the exact key set is the actual gate — a field nobody foresaw
  must not slip through on the grounds that nobody foresaw it;
- `submission_version` is checked **first** (O7), so a v2 client gets told it is
  a v2 client rather than being told its v2 fields are unknown.

O9 is the law that states the boundary directly: a client-supplied `run_result`
cannot reach the kernel under any spelling.

## 4. Nothing was executed, and the receipt says so

The server runs no agent. A candidate arrives already written. That is a
genuinely different provenance from `trvs eval-one`, where an agent ran under a
recorded toolchain on a recorded platform, and the receipt has to be able to
say which one it is.

Hence the runner profile `traaviis.ors-submission.v1`, in
`execfacts.NON_EXECUTING_PROFILES`:

```text
filesystem   not_applicable
network      not_applicable
termination  not_executed
exit_code    null
```

`not_applicable` is not `none`, and `not_executed` is not `exited`. A sandbox
policy of `none` asserts that a program ran unsandboxed; `not_applicable`
asserts there was no program to sandbox. Likewise `exit_code: null` — O13 is
the law that a null exit code is **not** read downstream as a failed run, which
is exactly the misreading that would have followed from fabricating a `0`.

O11 pins the other half: the adapter profile is a *new key*, and it moved no
existing episode. Every previously minted `episode-…` is byte-identical.

## 5. `finished: true` is a durability claim

The tool result reports `finished: true` only after the episode has been
staged, **re-verified as a reader would verify it**, fsynced, and published by
rename into `episode-<id>/`. If publication fails, the response is a failure —
not a finish with a warning (O18).

This is the one place where the transport is allowed to be opinionated, and it
should be: a client that reads `finished: true` and drops its local copy has to
be right. A "scored but not yet durable" state would be a lie with a latency.

## 6. Admission precedes binding

The whole admission path — open the packed environment, verify its closure,
resolve the split, confirm the episode output directory is writable — runs
**before the socket binds** (O24, O25).

A server that binds first and admits later has a window in which it is
reachable and cannot serve. That window is where clients learn to retry, and
retry loops outlive the bug that caused them. Failing before the port exists
means a misconfigured `serve` is a message on stderr and a non-zero exit, which
is what an operator can act on.

## 7. Idempotency is a transport concern, so it lives in a header

Retry safety is a property of the wire, not of the episode. `Idempotency-Key`
is therefore an HTTP **header**, not a submission field — putting it in the body
would have made it part of `RemoteSubmissionV1`, which is content the identity
ladder would then have to have an opinion about.

The rules are three laws:

| law | statement |
| --- | --- |
| O19 | the same key replays the stored answer instead of re-scoring |
| O20 | a *different* key after a finish is refused by name |
| O21 | a **keyless** second submission is refused too |

O21 is the one worth arguing about. The permissive reading — no key means no
idempotency contract, so just score it — turns a client that forgot the header
into a client that silently double-executes a candidate's test suite. A session
gets one shot; a missing key does not buy a second one.

## 8. Concurrency is §4a exercised, not re-solved

O22 (two concurrent submissions to one session score exactly once) and O23 (two
different sessions score simultaneously) are the kernel's K19–K26 seen through
the wire. The transport adds no locking of its own; it inherits the
linearization.

O23 is also where this slice found a real defect — see §10.

## 9. Loopback by default

The default bind is `127.0.0.1`. Leaving loopback requires `--allow-remote`,
explicitly (O26). The server accepts unauthenticated submissions that execute a
candidate's test suite against a packed environment; that is a perfectly
reasonable thing to do on a developer's machine and a completely unreasonable
thing to do on an interface by accident. The banner ends with `loopback only.`
so the state is visible without reading flags.

Body size is capped at 8 MiB.

## 10. Two defects this slice found

Both were found by the battery, not by the smoke test — which is the argument
for writing the battery first.

### The publish race (found by O23)

Two sessions scoring the **same deterministic content** each passed the
`os.path.exists(final)` check, each staged a complete bundle, and the loser's
`os.rename` hit `ENOTEMPTY`:

```text
OrsError: the episode scored but its evidence could not be published
  OSError: [Errno 39] Directory not empty:
    '.../.tmp-episode-dkka32nk' -> '.../episode-54d52ddf…'
```

The check-then-rename was TOCTOU. The fix does **not** add a lock — content
addressing already makes a collision a no-op — it takes the race as a signal:

```python
try:
    os.rename(tmp, final)
except OSError as exc:
    if exc.errno not in _RACE_ERRNOS or not os.path.exists(final):
        raise
    shutil.rmtree(tmp, ignore_errors=True)
    return _accept_existing()
```

`_accept_existing()` is the *same* idempotent-reuse path the existence check
takes, extracted so both routes share it. It re-verifies the winner and accepts
it only if it verifies closed **with this episode's id** — the loser never
trusts the winner by name. Renaming a directory onto a non-empty one fails
rather than clobbering, so the loser still holds a complete staged tree at the
moment it decides; nothing is lost either way.

This was reachable only from a concurrent transport, and it is the second
defect in two slices of that exact shape.

### The shutdown hang (found by O26)

`OrsHttpServer.shutdown()` called `self._httpd.shutdown()` unconditionally, and
`socketserver.BaseServer.shutdown()` waits on an event that only
`serve_forever` sets. A server that was **bound but never served** — which is
precisely what O26 constructs, and what any code path that binds to report a
port and then fails would produce — blocked forever in its own cleanup.

Binding and serving are separate here by design, so releasing a bound-only
socket has to be possible. A `threading.Event` now records whether
`serve_forever` is actually running; `shutdown` only waits if it is, and closes
the socket either way. `start_background` waits on the same event, so it returns
once the server is genuinely serving rather than once the thread exists.

## 11. A correction this slice made to its own laws

O30's first form scanned `identity.py` as raw text for words the module is
forbidden to know, and reported that it mentions `ors` — inside `separators`,
from a `json.dumps` call.

This is the **fifth** time in this codebase that a text scan has made a claim
about structure it could not see (K10, K12, K18, K28). The rule these have
converged on: *a module is allowed to name a seam it deliberately does not
cross, so a law about a seam has to parse rather than grep.* O30 now tokenizes
identifiers and compares whole words:

```python
ident_words = set()
for token in re.findall(r"[A-Za-z_]+", inspect.getsource(I)):
    ident_words.update(p for p in token.lower().split("_") if p)
```

A word this module is forbidden to know is a word, and the check has to be able
to tell one from a syllable.

The second harness fix was a latent crash: the law-numbering completeness check
read `int(n.split("_")[0][6:])` where `n` is `test_o30_…`, so it parsed `"test"`
and sliced it to `""`. It had never been reached because the assertion above it
always failed first — which is its own small lesson about assertion order.

## 12. A law that expired

`test_kernel.py`'s K18 asserted, among other things, that no CLI verb reached
the kernel — with the comment "`trvs serve --ors` is the next slice, not this
one". Shipping this slice made that clause false, correctly.

It was not deleted. What must not expire is *how* the kernel is reached, so the
clause was re-scoped: `cli.py` may not name `kernel` or `EpisodeKernelV1` at
all. It imports `ors` and `ors_server`. The CLI drives an adapter and the
adapter drives the kernel; a command that constructed a kernel itself would make
the CLI a second transport, and the two would drift.

## 13. Live end-to-end

A fresh scaffold, no fixtures:

```text
trvs init env --template evidence-residency     8 files
trvs pack env pkg                                env-a38ec4c04532be25…
                                                 snap-c66198ab… task-3b4b2599…
                                                 rew-25c4ce12… bundle-214fb799…
trvs serve pkg --ors --split all --output episodes --port 8791

POST /sessions                                   201
POST /sessions/<id>/call_tool                    200  reward 1.0  finished true
  Idempotency-Key: demo-1                        episode-0a258d6e4976ffd1…
  (repeat, same key)                             identical response
POST /sessions/<id>/observe                      501  KERNEL_OPERATION_UNSUPPORTED

trvs verify-episode episodes/episode-0a258d6e…   7/7 replay == receipt
                                                 reward 1.0 vs 1.0
                                                 verified ✓ closed
```

The last line is the point of the whole stack: the episode a remote client
produced replays offline, with no agent and no server, to the same reward.

## 14. Autonomous decisions (flagged for review)

Everything structural was ruled. Four choices were not:

1. **`_accept_existing()` is shared by both the check path and the race path.**
   The alternative — let the race path raise and make the caller retry — pushes
   a filesystem detail into the transport. Content addressing makes reuse
   correct; the only requirement is that reuse never trusts a name, and it does
   not.
2. **`shutdown()` closes the socket even when never served.** The alternative is
   to require `serve_forever` before `shutdown`, which makes every failure
   between bind and serve leak a socket.
3. **`start_background()` waits (5s) for the serving event.** Returning a server
   that is not yet accepting makes every caller write its own poll loop.
4. **The banner label is `runner`, not `runner profile`.** Cosmetic: `_field`
   pads to 13 characters and the longer label overflowed the column.

## 15. Cost

Two new modules (`traaviis/ors.py` 740, `traaviis/ors_server.py` 336), one new
battery (`test/test_ors.py` 1248), one new CLI verb (`cmd_serve`, +110 in
`cli.py`), and one bug fix in `episode_bundle.py`. `execfacts.py` gained the
non-executing runner profile. No new dependency — `http.server` and
`socketserver` are standard library. No new identity rung, no new receipt field,
no schema migration, no change to any existing episode.

## 16. Not started (deferred)

- `trvs serve --mcp` — the same kernel behind the MCP wire vocabulary
  (tools / resources / prompts). Deferred by ruling; the kernel and this
  adapter together are what make it a translation layer rather than a rewrite.
- Authentication, TLS, and multi-tenant session quotas. Loopback is the current
  answer and `--allow-remote` is deliberately blunt.
- The REPL, `EvaluationV2`, `eval-…`, `agent-…`, and batch-evidence
  distribution identity remain deferred.
