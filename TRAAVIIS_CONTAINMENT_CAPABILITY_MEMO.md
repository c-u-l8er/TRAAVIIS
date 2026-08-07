# 9F-A — containment capability honesty

**Status.** Implemented and measured on this box, 2026-08-06.

**What this is.** 9E claimed adversarial containment. It had an observed kill
boundary whose control plane the candidate could reach. This slice does not add
containment — it makes the system say what it has, refuse what it cannot do, and
stop charging the host's shortcomings to the candidate.

---

## 0. The retraction

9E's memo said:

> A cgroup v2 membership cannot be left.

**That is false.** Reproduced on the host that shipped it:

```
cgroup=…/app-….scope/traaviis-run-2289503-1
parent=…/app-….scope
parent writable=True
MIGRATED-TO-PARENT ok

run exit=0  enforced=True  surviving=0
DESCENDANT ALIVE AFTER RUN: True
```

The candidate read `/proc/self/cgroup`, wrote its pid to the **parent's**
`cgroup.procs`, double-forked, and exited 0. The run then reported itself
contained while a descendant it had lost was alive.

That was not an incomplete record. It was a **false positive safety assertion** —
the one outcome the 9F ruling names as never acceptable.

The narrower true statement, which is what the code now rests on:

> A process cannot remove *itself* from its cgroup — so a `setsid` descendant is
> still a member and `cgroup.kill` still reaches it. A delegatee cannot move
> processes *outside its delegated subtree*; **within** that subtree it may
> reorganize processes and create sub-cgroups as permitted. A same-uid candidate
> is inside the delegated subtree with the evaluator.

The false sentence is retracted in all three places it was written: the 9E memo
(which now opens with the retraction), `execlimits`' module docstring, and
`test_containment`'s Y2.

---

## 1. Capability axes replace one overloaded boolean

`enforced` answered four questions at once, and answered them by inference:
nothing was seen to escape, so containment was reported.

```json
{
  "profile": "traaviis.cgroup-kill-observed.v1",
  "kill_boundary_enforced": true,
  "remaining_processes_in_boundary": 0,
  "control_plane_isolated": false,
  "resource_controllers_enforced": false,
  "cleanup_closed": true,
  "certifiable": false,
  "uncertifiable_reasons": [
    "candidate_can_reach_parent_cgroup",
    "memory_controller_unavailable",
    "pids_controller_unavailable"
  ]
}
```

`certifiable` is the **conjunction**. `UNKNOWN` is a third value — a capability
that could not be tested — and it never satisfies the conjunction. *Do not infer
success from an absent failure* is the rule, and `UNKNOWN` is what makes it
representable rather than aspirational.

The profile is renamed to **`traaviis.cgroup-kill-observed.v1`**. `cgroup-v2`
sounded like the whole mechanism; this name says what was measured — processes
still *in that cgroup* were killed, and nothing was shown about processes that
left it. `LEGACY_CGROUP_PROFILE` keeps the old string readable so the rename is
legible rather than a silent substitution.

---

## 2. A missing boundary refuses; it does not punish

The most important correction, and 9E had it backwards.

9E recorded `/resource-limit.v1:containment_unavailable` as a policy violation:
it ran the candidate and *then* marked the episode invalid — charging a
submission for the evaluator host's topology. The submission did nothing.

```
boundary could not be established at all
    -> no evaluation, no episode, exit 2, candidate never started

candidate misbehaves inside a boundary that WAS established
    -> invalid / reward 0
```

`containment.preflight(strict=True)` raises `ContainmentUnavailable` carrying the
failing axes, before anything spawns. It is a *dry run of the boundary*: the
cgroup is created, its control plane probed, its controllers listed, and it is
removed — so no axis can be reported that was not exercised. The
`containment_unavailable` violation is **gone** from `run_agent`;
`processes_escaped` stays, because a process that escaped a boundary the
evaluator did establish is evidence about the candidate.

---

## 3. Two profiles, and no episode identity moved

```
residency.certified-local.v1              requires a passing preflight
residency.trusted-local-best-effort.v1    kill boundary, no containment claim
```

Both are new **keys** in `RUNNER_PROFILES`, never new fields on an existing
profile — `build_execution_facts` seals `dict(RUNNER_PROFILES[profile])` straight
into `episode-…`, so a new field would move every identity ever minted under
that profile. `test_ors::O11` is the identity law that guards this and it caught
the change, which is the law working.

The default runner profile stays `residency.trusted-local.v1`. Renaming it to the
explicit best-effort name would move the whole corpus for a cosmetic gain, so it
keeps its historical name and is *classified* as best-effort:
`BEST_EFFORT_PROFILES` contains both, `STRICT_COMPARISON_PROFILES` contains only
the certified one, and `strict_comparison_eligible` refuses an **unknown**
profile — defaulting an unclassified profile to eligible would be the same
inference-from-absence that `enforced` made.

`compare_episodes(strict=…)` refuses a best-effort episode, reading the profile
out of the receipt rather than accepting it from the caller. It **defaults to
False**, deliberately: every episode in this tree today is best-effort, so a
default of True would refuse the entire corpus and the flag would be switched off
rather than understood. The default flips at the certified-backend cutover.

---

## 4. Laws

`test_containment.py` — **24 passed, 0 skipped, 0 failed**.

* **Y22** the parent-migration exploit, executable. Forbids the conjunction
  *(exit 0 ∧ descendant alive ∧ certifiable)*, and cross-checks the probe against
  the measurement in both directions. Non-vacuous: planting 9E's overclaim turns
  it red with the right message.
* **Y23** the run must report whether it could *bound*, not only kill.
* **Y24** candidate-created nested cgroups are torn down, depth-first and
  bounded; `nested_cgroups_removed` is evidence the candidate touched the control
  plane even when it failed to leave.
* **Y20** rewritten for the reversed semantics: preflight refuses, and
  `run_agent` must **not** record the missing boundary — checked on the parse
  tree, since the absence of a string is exactly what a comment could restore.

Full battery: **794 passed, 0 skipped, 0 failed** (35 files).

### Sibling migration

Y22 attempts parent migration and falls through to sibling creation when the
parent write is refused. On this host the parent write succeeds, so the sibling
path is exercised only where it is the reachable one. Stated rather than
implied: the sibling branch is *written* and, here, not *reached*.

---

## 5. Three defects of my own, and how each surfaced

| defect | how it was found |
| --- | --- |
| facts captured **before** teardown, so `nested_cgroups_removed` always read 0 | reading the output of a probe I had just written |
| Y22 read `facts.get("control_plane_reachable")`, a key I had renamed — `get` returned `None`, the law took the wrong branch and went red claiming a candidate escaped an *isolated* containment | the failure message made no sense |
| W2's probe disables containment deliberately, then reaped only the pid it recorded — a hung worker outlived the battery by 3½ minutes holding a cgroup open | looking at the cgroup directory |

The second is the interesting one: **a missing measurement read as a
measurement**, which is the same defect as inferring success from an absent
failure, one level up in the test. Y22 now asserts the key is present before
reading it.

### And two duplication findings

`compare_episodes`'s parameter set is pinned in **three** batteries
(`C15d`, `B29`, `D29`). One deliberate surface change needed three edits and a
missed one is a false red. Recorded in D29 rather than made a law: a duplicated
*pin* fails loudly, unlike the duplicated *guard* X21 forbids.

`D29` was registered under Law B as `prose`, with the rationale *"reads another
battery's stdout"* — which is not what it does. It did
`"bundle_id" not in inspect.getsource(module)`, a substring claim over source,
i.e. a genuine violation classified as legitimate. That is the
*registered exception → normalized exception* drift, sitting in the register.
It is AST-based now and the entry is deleted.

---

## 6. What this host still cannot do

Unchanged from the 9E report, and now reported by the code rather than by prose:

```
control_plane_isolated          false   parent cgroup is writable by the candidate's uid
resource_controllers_enforced   false   parent holds 61 processes, so cgroup v2's
                                        no-internal-process rule forbids enabling
                                        subtree_control; the run cgroup gets nothing
```

Namespace hardening is not attempted. `CLONE_NEWCGROUP` needs `CAP_SYS_ADMIN`, so
it needs a user namespace; this session is already inside one whose `uid_map` is
unwritable, so the process becomes `nobody` and the agent cannot write its own
workspace. Writing that path here would produce a security claim supported by
reasoning and not by an exploit law — which is exactly what 9E did.

The certified backend is a **deployment requirement**, not another patch to
`run_bounded`.
