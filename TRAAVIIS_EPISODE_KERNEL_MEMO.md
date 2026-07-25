# Episode Kernel Closure — memo

**Slice:** the kernel extracted *before* the transport, exactly in that order.
**Status:** shipped.

**Battery.** K1–K18 with the engine present: **18 passed / 0 skipped /
0 failed**; engine absent: **17 passed / 1 skipped / 0 failed** (only K14 packs
a real template — see §3). Whole tree: **492 passed / 0 skipped / 0 failed**
across 27 files, up from 474/26.

Ruled by the sixth GPT-5.6 ruling, second half:

```
EpisodeKernelV1 { list_tasks() start(task_id) observe(session_id)
                  step(session_id, action) reset(session_id)
                  finalize(session_id, run_result) close(session_id) }
```

with `session_id` an ephemeral process handle, Residency v1 supporting only
`start` + `finalize`, the local command runner demoted to an adapter that
reproduces the current `eval-one` receipt **byte for byte**, and the process
model *one kernel = one admitted environment, many ephemeral sessions, one
shared registry, one shared engine seam, no process-wide lock over a session
lifetime*.

---

## 1. Why the order is the whole point

A kernel written *after* a server is a description of what that server happened
to need. A kernel written *before* one is a statement about what an episode is,
which the server then has to translate into. The two artifacts can look
identical and mean opposite things, and there is no test that distinguishes them
after the fact — only the order does.

So this slice adds no capability. Nothing `trvs` can do today it could not do
yesterday; there is no new verb, no new rung, no new receipt field. What changed
is that "an episode is admission, then a run, then scoring" is now a **shape**
with a name, rather than the order of statements inside one function.

The single most useful consequence is already visible and is not about servers
at all: because `observe` is a *refusal* under Residency, a remote client cannot
read a Residency session. So `trvs serve --ors` over Residency v1 can honestly
expose only `start` + `finalize`. That is now a consequence of the kernel rather
than a decision the server author would have had to make — and would probably
have made the other way, by inventing a plausible observation.

## 2. What was built

| file | change |
| ---- | ------ |
| `traaviis/kernel.py` | **new.** `EpisodeKernelV1`, `ResidencyKernelV1`, `SessionV1`, `TaskEntryV1`, `KernelError`, `local_kernel`, `environment_kernel`, `run_episode`. |
| `traaviis/evalone.py` | pipeline split into `_admit_episode` / `_invalid_config_run` / `_finish_episode`; `evaluate` rewritten as the local command adapter. Public surface unchanged. |
| `traaviis/evalsplit.py` | `eval_split` opens **one** kernel for the environment; `_run_one` drives one ephemeral session per task. |
| `test/test_kernel.py` | **new.** K1–K18. |
| `RFC_TRAAVIIS_ARTIFACTS.md` | new **§4a** freezes the kernel; §4 and §7 point at it. |
| `README.md` | "the environment surface" is no longer a roadmap section. |

### Where the cut was made

At the exact `runner.run_agent` line inside `evaluate`. Everything above it is
*what an episode is* — admission, cross-binding, policy honesty, verifier-version
sealing, the F4 configuration preflight. Everything below is *what came back*.
Lifting the subprocess out from between those two halves, without reordering
either, is why the receipt is byte-identical **by construction** rather than by
a comparison that happened to pass.

```text
_admit_episode(task, content, reward_spec, …)  →  plan
        │                                            (no process launched yet)
        ├── plan["unresolved"]  →  _invalid_config_run(plan)      status=invalid
        │
        └── runner.run_agent(…)  →  _finish_episode(plan, run, …) → EvaluationRunV1
```

### The interface is a declaration

`EpisodeKernelV1` implements none of the seven operations and refuses all of
them; support is `supported_operations` and nothing else. A base class that
quietly implemented one would make "this substrate supports X" a property of
which method a subclass remembered to leave alone. `describe()` reports
`{kernel_version, substrate_profile, operations}`, so a client can ask what it
is talking to before it asks for anything.

### A session is not an identity

`session-<hex>`, freshly random per `start`. `identity.py` mints nothing for it,
no artifact references it, and K5 proves it reaches neither the run document,
nor the canonical receipt bytes, nor any file *name* or *content* inside a
written episode bundle. Two `start` calls on one task return two handles and one
`episode-…`.

### Refusals, not no-ops

| code | when |
| ---- | ---- |
| `KERNEL_OPERATION_UNSUPPORTED` | `observe` / `step` / `reset` on Residency; anything on the base |
| `KERNEL_TASK_UNKNOWN` | `start` / `entry` for a task outside the closure |
| `KERNEL_SESSION_UNKNOWN` | a released or never-issued handle |
| `KERNEL_SESSION_STATE` | finalizing a finalized session |
| `KERNEL_RUN_RESULT_MISSING` | a runnable session finalized with `None` |
| `KERNEL_RUN_RESULT_UNEXPECTED` | a non-runnable session finalized with a `RunResult` |
| `KERNEL_SUBSTRATE_UNSUPPORTED` | `environment_kernel` over `trvm.world.v1` |
| `KERNEL_REWARD_UNRESOLVED` | a task whose reward is not in the package |
| `KERNEL_TASK_UNIDENTIFIED` | `local_kernel` over a task no `task-…` can be derived from |

`KernelError` inherits from **both** `substrates.AdmissionError` (typed:
`code`, `message`, `detail`) and `admission.AdmissionError` (the plain one), so
every pre-existing `except` clause keeps its meaning. That matters concretely:
`evalsplit._run_one` catches `admission.AdmissionError` in order to record a
*failed episode* rather than abandon the split, and a kernel refusal must not
change one bad task into an aborted run.

## 3. K1–K18

| # | law | test |
| - | --- | ---- |
| K1 | the interface is the frozen seven; the base refuses all of them | `test_k1_the_interface_is_the_frozen_seven_and_the_base_refuses_all_of_them` |
| K2 | Residency supports `list_tasks`/`start`/`finalize`/`close`, refuses the rest | `test_k2_residency_supports_start_and_finalize_and_refuses_the_rest` |
| K3 | the interactive trio are refusals, never no-ops | `test_k3_the_interactive_operations_are_refusals_never_no_ops` |
| K4 | a session id is an ephemeral handle, not an artifact id | `test_k4_a_session_id_is_an_ephemeral_handle_not_an_artifact_id` |
| K5 | a session id is never persisted into an episode | `test_k5_a_session_id_is_never_persisted_into_an_episode` |
| K6 | a session is ephemeral and `close` is idempotent | `test_k6_a_session_is_ephemeral_and_close_is_idempotent` |
| K7 | `finalize` refuses an unknown or already-finalized session | `test_k7_finalize_refuses_an_unknown_or_already_finalized_session` |
| K8 | `finalize` refuses a missing or an unexpected run result | `test_k8_finalize_refuses_a_missing_or_unexpected_run_result` |
| K9 | `start` refuses an unknown task and opens no session | `test_k9_start_refuses_an_unknown_task_and_opens_no_session` |
| K10 | three paths to an episode produce the identical receipt | `test_k10_every_path_to_an_episode_produces_the_identical_receipt` |
| K11 | a kernel episode replays to the identical receipt | `test_k11_a_kernel_episode_replays_to_the_identical_receipt` |
| K12 | the subprocess boundary has exactly one door | `test_k12_the_subprocess_boundary_has_exactly_one_door` |
| K13 | an invalid-config session opens, runs nothing, and still scores | `test_k13_an_invalid_config_session_opens_runs_nothing_and_still_scores` |
| K14 | one kernel serves one admitted environment | `test_k14_one_kernel_serves_one_admitted_environment` |
| K15 | many ephemeral sessions may be open at once | `test_k15_many_ephemeral_sessions_may_be_open_at_once` |
| K16 | no process-wide lock is held over a session lifetime | `test_k16_no_process_wide_lock_is_held_over_a_session_lifetime` |
| K17 | a substrate with no episode semantics is refused by name | `test_k17_a_substrate_with_no_episode_semantics_is_refused_by_name` |
| K18 | the ladder, the CLI and the earlier laws are untouched | `test_k18_the_ladder_the_cli_and_the_earlier_laws_are_untouched` |

Only **K14** needs a Forge checkout, because it is the only law that is
genuinely about a *packed environment*: it scaffolds and packs the real
`residency-repair` template, spies `environment_kernel` and
`ResidencyKernelV1.start`, and asserts one kernel object, one session per task,
distinct handles, and an empty session table at the end. Everything else runs
against the deterministic stub agent with injected verifiers.

K10 is the ruled byte-for-byte law, taken from three independent directions:
the adapter (`evaluate`), the receipt-only wrapper (`eval_one`), and a
hand-driven `start → run_agent → finalize`. K11 is a fourth and better one,
because it goes through code this slice never touched:
`verify_episode_bundle` rebuilds a *fresh* receipt from saved evidence through
the same `build_receipt_v1`, with no kernel anywhere in the path, and it comes
back identical.

K16 is the process-model law with teeth: it acquires the kernel's lock
non-blockingly from a second thread *while a session is open*, and additionally
counts `with self._lock:` blocks in the source — four, all of them around table
bookkeeping, none around admission, scoring, or a subprocess.

## 4. A defect this slice created and then closed

Three of the K-laws were written as **textual** source scans, and two of them
were wrong for the same reason: a module is allowed to *name* a seam it
deliberately does not cross. `evalone.evaluate`'s docstring says it drives
`runner.run_agent`, and `kernel.py`'s docstring says a receipt is built by
exactly one piece of code, `build_receipt_v1`. Both sentences are the statement
of the boundary. A `"run_agent" not in source` test called them crossings.

Closed by parsing instead of grepping: `_run_agent_calls` walks the AST and
reports `ast.Call` nodes only; `_identifiers` collects the names a module
actually *references*. Both laws now say what they meant — and are strictly
stronger, since a call spelled across two lines would have slipped past the
line-oriented scan that "passed".

This is the same shape as the two previous slices' defects: a claim that was
checked against a rendering of the code rather than against the code.

## 5. Autonomous decisions (flagged for review)

1. **`list_tasks` and `close` are first-class kernel operations**, not helpers.
   The ruling names all seven; RFC §4's older sketch listed five. §4a states the
   built seven and says so explicitly.
2. **`KernelError` inherits from both `AdmissionError` classes.** The package has
   two, and a refusal that only satisfied one would silently change
   `evalsplit`'s per-task failure handling into an aborted split.
3. **An invalid configuration still opens a session** (`runnable = False`) and is
   scored at `finalize(sid, None)`. Refusing to open it would turn a scored
   `status=invalid` outcome into a crash, which is a different claim about the
   task.
4. **The kernel never learns the agent command.** `start(task_id)` matches the
   ruled signature exactly; the kernel hands out `content` + `policy` and
   consumes a `RunResult`. This is what leaves `serve --ors` a translation layer
   rather than a second runner.
5. **`run_episode(kernel, task_id, agent_command)` is a module function, not a
   method.** It is the *adapter*, and the ruling puts the runner outside the
   kernel; making it a method would put the subprocess back inside.
6. **`evalone` imports nothing from `kernel` at module scope; `kernel` imports
   the three phase functions from `evalone`.** The dependency points one way,
   and `evaluate` takes the lazy in-function import. The alternative — a fourth
   module holding the phases — moves more code for the same graph.
7. **`eval_split` now emits every task's wiring note at kernel construction**,
   before the first agent runs, rather than interleaved with episodes. This is a
   side effect of admitting the environment once. It is arguably an improvement
   (all configuration problems are visible before any agent starts) and it is
   consistent with the module's own fail-before-launch doctrine, but it is an
   observable change in output order, so it is flagged.
8. **`ResidencyKernelV1.close` returns a bool** (was this handle open?) rather
   than raising on an unknown id. Releasing a handle twice is not an error, and
   an adapter that closes in a `finally` must not raise over an already-released
   session.

## 6. Cost

None measurable: the same work happens in the same order. The refactor costs one
extra object per episode and one dictionary insertion and deletion per session.
`eval_split` now holds all task entries for the environment in memory for the
duration of the split rather than assembling each in turn — for the environments
that exist, that is a task document, a reward spec and a snapshot per task.

## 7. Not started (deferred by ruling)

Next is **`trvs serve --ors`** as a translation layer over this kernel — which,
per §1, can honestly expose only `start` + `finalize` for Residency v1 and must
relay `KERNEL_OPERATION_UNSUPPORTED` for the rest.

Still deferred: MCP, batch-evidence distribution identity, `eval-…`, `agent-…`,
the REPL, `EvaluationV2`.
