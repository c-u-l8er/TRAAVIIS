# Finalize Linearization Closure — memo

**Slice:** make `finalize` linearizable, so a concurrent transport cannot make
one session run its verifier plan twice.
**Status:** shipped.

**Battery.** K1–K28 with the engine present: **28 passed / 0 skipped /
0 failed**; engine absent: **26 passed / 2 skipped / 0 failed** — K14 and K27
are the only two that pack a real template, and they SKIP honestly rather than
weakening. Whole tree: **502 passed / 0 skipped / 0 failed** across 27 files,
up from 492/27.

Ruled by the seventh GPT-5.6 ruling, which accepted the Episode Kernel semantics
and blocked `trvs serve --ors` on exactly one defect. GPT reproduced it directly
against the shipped packet — two threads inside `_finish_episode`, two
successful finalizations, zero refusals, the same receipt returned twice — and
froze the lifecycle, the claim algorithm, the `close` refusal, the terminal
failure rule, and laws K19–K28. This slice implements that ruling and nothing
else.

---

## 1. The defect, stated precisely

The kernel's lock was already short, and short was the right instinct: a kernel
that locks for the duration of a run serializes a server down to one episode at
a time and deadlocks the moment a session outlives a request. K15 and K16
enforced that, and they were right to.

But **a short lock is not the same as an atomic transition**, and finalization
is a one-shot transition. The old code read the state in one statement:

```python
if session.state != SESSION_STARTED:
    raise KernelError("KERNEL_SESSION_STATE", ...)
```

and wrote it in a much later one, with the entire verifier plan in between.
Two callers could both pass the check before either wrote. The second was not
refused; it re-ran the plan. For a Residency task that means **executing a
candidate's test suite a second time**.

The reason this is worth a slice of its own rather than a line of hardening is
the shape of the failure. Both callers receive the **same receipt** — the same
`episode-…`, the same reward, the same verifier outcomes — because the episode
is deterministic. Nothing downstream looks wrong. The identity does not move;
only the work doubles. A benchmark that quietly executes half its candidates'
test suites twice reports correct numbers and burns twice the compute, and the
first symptom is a flaky verifier that had a side effect nobody documented.

It is also unreachable from the shipped surface. `trvs eval-one` and `trvs eval`
are sequential, so every one of K1–K18 passed. It becomes reachable the instant
a transport accepts two requests at once — which is the *next* slice. Catching
it before the server is written rather than after is the same argument as
extracting the kernel before the transport.

## 2. What was built

`traaviis/kernel.py` only. No other module changed; no identity moved; no CLI
verb appeared.

### The lifecycle is four states, and `closed` is not one of them

```text
started ──claim──► finalizing ──► finalized
                              └──► finalize_failed

started / finalized / finalize_failed ──close──► forgotten
finalizing ────────────────────────────close──► KERNEL_SESSION_BUSY
```

`SESSION_STATES` and `SESSION_CLOSEABLE` are exported so a transport can reason
about the lifecycle without re-deriving it. `closed` is deliberately absent: a
closed session is *forgotten*, not retained, so "closed" is spelled by the
session's absence from the table. Adding a `closed` state would have made the
kernel a session store, which is a different object with a different lifetime.

### The claim is atomic; the work is not inside it

```python
def _claim_finalize(self, session_id, run_result):
    with self._lock:
        session = self._sessions.get(session_id)          # exists?
        ...  # state == started?  run result matches runnable?
        session.state = SESSION_FINALIZING                # claimed
        return session
```

Everything expensive — admission, the verifier plan, the subprocess-free scoring
— runs **outside** the lock. The lock is re-taken only to record the outcome:

```python
def _release_finalize(self, session, state, result=None):
    with self._lock:
        session.state = state
        session.result = result
```

so `finalize` is claim → work → release, and exactly one caller can leave
`_claim_finalize` holding the claim.

`SessionV1` gained one slot, `result`, holding the `EvaluationRunV1` a
successful finalization produced. It exists so a caller that reaches the session
*after* finalization reads the answer rather than being tempted to recompute it.

### Three refusals, and one of them deliberately does not consume the shot

| refusal | when |
| --- | --- |
| `KERNEL_SESSION_STATE` | the session is `finalizing`, `finalized` or `finalize_failed` |
| `KERNEL_SESSION_BUSY` | `close` on a session another caller is finalizing |
| `KERNEL_RUN_RESULT_MISSING` / `_UNEXPECTED` | the run result contradicts `runnable` |

The run-result refusals are checked **inside** the claim (so they are evaluated
against a consistent state) but leave the session `started`. Passing the wrong
argument is a caller error that ran nothing, and it must not burn the session's
single chance to be scored. Only *entering the scoring work* consumes the shot.

`close` refusing a `finalizing` session is not fastidiousness. Removing the
entry would not stop the work; it would only make the result unattributable, and
it would let a third caller `start` past a test suite that is still executing.

### A failed finalization is terminal

`finalize_failed` cannot be retried. Verifiers and test commands have external
side effects, so a retry is a second execution wearing the first one's name.
The honest recovery is a **new session** — which is a new episode, and says so.
The `except BaseException:` that records the failure re-raises; it changes the
session's state and nothing about the exception.

## 3. K19–K28

| law | statement |
| --- | --- |
| K19 | two simultaneous finalizations produce exactly **one** claimant, and the plan executes **once** |
| K20 | the losing call is refused **by name** (`KERNEL_SESSION_STATE` / `KERNEL_SESSION_BUSY`) |
| K21 | the scoring work runs at most once per session, across any call sequence |
| K22 | `close` on a `finalizing` session is refused, and the session survives |
| K23 | a failed finalization leaves a terminal `finalize_failed` session |
| K24 | a `finalize_failed` session cannot be retried |
| K25 | two **different** sessions may be inside the scoring work at the same time |
| K26 | no lock is held while verifiers execute — proven under load, not by reading source |
| K27 | local `evaluate` / `eval_one` / hand-driven / `eval_split` receipts are unmoved |
| K28 | the patch added no rung, no CLI verb, no receipt field, and exactly one refusal code |

Two things about how these are written are load-bearing.

**The race is forced, not hoped for.** `_HeldScoring` replaces `_finish_episode`
with a wrapper that blocks, so a second caller is *guaranteed* to arrive
mid-flight. That makes the law deterministic in both directions: it fails every
run against the pre-patch kernel and passes every run against this one. A test
that spawns two threads and hopes they collide is a test that passes for the
wrong reason most of the time.

**The counter is the point.** `_HeldScoring` records every entry into the
scoring work, because "exactly one finalization succeeded" is strictly weaker
than "the work ran once" — the old code satisfied the first (both callers got
the same receipt) while violating the second. Asserting only on return values
would have declared the defect fixed while it was still there.

**K25 and K26 are the other direction.** The obvious way to "fix" a race is to
hold the lock, which would make K19–K24 pass and quietly reintroduce the
one-episode-at-a-time server K15/K16 exist to prevent. K25 puts a
`threading.Barrier` *inside* the scoring work, so both sessions must be there
simultaneously for it to clear — a kernel that serialized finalization would
**deadlock** this law rather than merely slow it. K26 asserts the lock is
actually free mid-flight (`k._lock.acquire(blocking=False)`) and drives a whole
second episode to completion while the first is suspended.

**K27 pins the pre-patch identity.** `PRE_LINEARIZATION_EPISODE_ID` is the
`episode-…` this fixture minted before the patch, recomputed from the shipped
`TRAAVIIS_EPISODE_KERNEL_CLOSURE` packet. Every input to it is
fixture-determined — the declared toolchain literal, the passed platform string,
the stub agent's deterministic output, the injected verifier versions, the agent
exit code — so it is host-independent, which is what makes it safe to freeze.
This is the one check a later slice cannot satisfy by moving both sides of a
comparison, which is precisely the failure mode of "compare two fresh
derivations and see that they agree".

## 4. A correction this slice made to its own laws

K28's first form scanned the kernel source for `"KERNEL_[A-Z_]+"` string
literals to assert the refusal vocabulary grew by exactly one. It failed,
reporting an eleventh code: `KERNEL_VERSION` — which is a *name* in that module,
exported in `__all__`, and matches any plausible pattern for a code.

This is the fourth time in this battery that a text scan has made a claim about
structure it could not actually see; K10, K12 and K18 each needed the same
correction, for a docstring sentence, a docstring line and a comment
respectively. The rule the battery has now converged on is worth stating: **a
module is allowed to name a seam it deliberately does not cross**, so a law
about a seam has to parse rather than grep. K28 now reads the codes out of the
`KernelError(...)` constructions themselves via the AST, and additionally
asserts each code is a **literal** — a computed code would make the vocabulary
uncheckable by any means.

The other harness fixes were mechanical: `_law_names()` (which both K18 and K28
call) reads the laws out of `globals()` rather than a hand-kept list, so a
deleted or renamed law fails the completeness check instead of silently
shrinking the battery; `RESIDENCY_AGENT` was hoisted to module scope now that
K14 and K27 both drive a packed environment; and `_packed_package()` memoizes
the scaffold+pack so the two laws share an *input* rather than state (each still
evaluates into its own output directory).

K16 also needed updating rather than fixing: the lock-block count moved 4 → 6
(`open_sessions`, `session`, `start`, `_claim_finalize`, `_release_finalize`,
`close`), which is exactly the shape of change the law is meant to force a human
to look at.

## 5. Autonomous decisions (flagged for review)

Everything structural here was ruled. Four small choices were not, and they are
where a reviewer should look:

1. **`SessionV1.result` retains the `EvaluationRunV1`.** The ruling says to
   record "`FINALIZED` + result"; it does not say whether the result stays
   readable. It does, on the session, until `close`. The alternative — discard
   it and make the caller keep the return value — makes a transport that loses a
   response unable to answer "what did this session produce?" without
   recomputing, which is the exact thing this slice exists to prevent.
2. **The run-result refusals leave the session `started`.** The ruling places
   the validation inside the claim but does not say whether a failed validation
   consumes the session. It does not. See §2.
3. **`SESSION_STATES` and `SESSION_CLOSEABLE` are exported.** So the ORS adapter
   can reason about the lifecycle without duplicating it. They are tuples in
   lifecycle order, and K28 pins both.
4. **`close` on `finalize_failed` succeeds** (it is in `SESSION_CLOSEABLE`). A
   terminal failure is still a finished session; refusing to close it would leak
   the entry for the process's lifetime.

## 6. Cost

One module changed (`traaviis/kernel.py`, +206/−~40 including docstrings), one
battery extended (`test/test_kernel.py`, K19–K28 plus three shared helpers),
two documents updated (RFC §4a, README). No new dependency, no new file in the
package, no schema change, no migration. The kernel remains dependency-free
standard-library Python.

## 7. Not started (deferred by ruling)

- `trvs serve --ors` — **this is now unblocked and is the next slice**
  (Residency Submission ORS Profile v1, battery O1–O30).
- `trvs serve --mcp`, the REPL, `EvaluationV2`, `eval-…`, `agent-…`, and
  batch-evidence distribution identity remain deferred.
