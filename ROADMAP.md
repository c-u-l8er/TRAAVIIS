# TRAAVIIS — what to build after the MCP transport

**Status.** Research and argument, not a ruling. Written 2026-08-04 against the
tree at `aba905f` plus the uncommitted `serve --mcp` work in flight. Nothing here
is implemented; several items below argue *against themselves* and one argues
that the most valuable next move adds no feature at all.

**Method.** Everything about the codebase in §0 was measured on this box today,
not recalled. Everything about the outside world carries a URL. Where a source
could not be fetched — three pages returned 403 — that is said in the line that
uses it, because a number quoted from a secondary report is a weaker fact than
one read from the page that produced it, and the difference should survive into
whatever gets built on top of this.

---

## 0. Measured state, 2026-08-04

```
python3 tools/run_battery.py
530 passed, 0 skipped, 2 failed  (28 files)
  FAIL test_o25_the_episode_output_is_mandatory_and_proven_writable_at_startup
  FAIL test_o26_the_default_bind_is_loopback_and_leaving_it_is_an_explicit_act
```

Three prior counts exist for this tree and none of them is today's.
`TRAAVIIS_ORS_CLOSURE_MEMO.md` §Battery says **532 / 0 / 0**.
`STACK_COMPLETION.md` (2026-07-29) says **530 passed, 1 real failure** and names
that failure `test_kernel.py::K27`. Today the kernel battery is **28/0/0** and
K27 passes; the two failures are elsewhere. The honest reading is not that any of
the three is wrong but that **the count is a function of the tree and the host at
the moment it was taken**, and quoting one without its date is how a repository
comes to have three totals for one suite.

Both of today's failures are traceable, and neither is a defect in shipped
behaviour:

- `git status` shows `?? traaviis/mcp.py`, `?? traaviis/mcp_server.py` and
  `M traaviis/cli.py`. The `serve --mcp` work is in this working tree right now.
- **O26** asserts `host.default == "127.0.0.1"` on the argparse action. The MCP
  work changed that default to `None` — deliberately, and with a comment saying
  why: `--mcp` has no bind address, so `serve` needs to distinguish "the user
  passed `--host`" from "the user did not" in order to refuse the combination by
  name. The loopback default still holds; it moved one layer down, to
  `host = "127.0.0.1" if args.host is None else args.host` in `_serve_ors`.
- **O25** asserts `{"ors", "split", "output"} <= required`. `--ors` is now a
  member of a mutually-exclusive protocol group and is therefore no longer
  `required`; the observed set is `{"split", "package", "output"}`.

So both laws are **checking the wrong location for a rule that still holds** —
the same shape as K18 ("no CLI verb reaches the kernel", made false by shipping
`serve --ors` and correctly *re-scoped* rather than deleted) and O30 (a text
scan that reported `identity.py` mentions `ors`, from inside `separators`). The
required action is to re-scope them so they assert the *resolved* default and the
*effective* requirement, not to delete them and not to relax them. That belongs
to whoever finishes `--mcp`; it is noted here so it does not get closed as
"expected failures during a slice" and stay closed.

Other measurements:

| | |
| --- | --- |
| package | 32 modules, 12,955 lines |
| batteries | 28 files, 13,189 lines |
| tools | 3 files, 791 lines |
| runtime dependencies | 0 |
| identity function | `sha256(json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8"))` |
| `pip install traaviis` | **`https://pypi.org/pypi/traaviis/json` → HTTP 404.** The name is unclaimed; the README's first code block does not work for anyone. |

Two behaviours worth having in front of you before reading the candidates,
because two items below turn on them:

**`not_applicable` and `fail` produce the same number.** `traaviis/reward.py`
`score()` adds a signal's weight iff its state is `pass`. A `fail` contributes
0.0; a `not_applicable` on a *non-required* scored signal also contributes 0.0.
The distinction is preserved in the receipt's `verification` map (and therefore
in `episode-…`), so the *evidence* is honest — but the *score* is not, and no
consumer reading `reward` alone can tell an agent that got a signal wrong from a
verifier that declined to answer. `error` is handled correctly and separately
(`reward = None`, never 0); a required signal in `not_applicable` is
`status = invalid`. It is exactly the optional-signal case that collapses.

**The runner is honest and is not a sandbox.** `execfacts.RUNNER_PROFILES`
declares `residency.trusted-local.v1` as `filesystem: "observed"` and
`network: "unrestricted"`, and `runner.py` refuses to seal a policy claiming a
stronger posture than the profile delivers. That is the right design and it is
rare. It also means TRAAVIIS today **cannot prevent an agent from fetching the
answer over the network** — it can only decline to claim that it tried.

---

## 1. What the outside world looks like in 2026

Five findings from this pass changed the ranking below. They are stated with
their evidence because two of them argue *against* TRAAVIIS's positioning.

**(1) The field's trust mechanism is re-execution, and it just shrank.**
Princeton's HAL was the only leaderboard that took agent *code* rather than a
reported score and ran it itself — 26,597 rollouts, cost-aware, full traces
published (encrypted, to stop the traces contaminating the next model)
[hal.cs.princeton.edu](https://hal.cs.princeton.edu),
[arXiv:2510.11977](https://arxiv.org/abs/2510.11977). **`hal-harness` was
archived read-only on 2026-07-01** and leaderboard updates are paused; the team
moved to HAL Reliability and `pass^k`. Terminal-Bench now requires ATIF
trajectories and runs an agent judge over passing trials
([leaderboard integrity update, 2026-04-19](https://www.tbench.ai/news/leaderboard-integrity-update)),
which is post-hoc adjudication rather than re-execution. So the one mechanism
that actually verified numbers got more expensive and less available in the same
year the evidence for needing it peaked. **Content addressing is the cheaper
substitute**: a third party who holds the ids checks hashes and replays the
verifier instead of re-purchasing the agent run. That is the strongest single
argument for this product and it did not exist in this form eighteen months ago.

**(2) Nothing in the mainstream stack is content-addressed.** This was checked
across seven systems and the answer was the same each time. SWE-bench pulls
mutable DockerHub tags; Epoch's own harness writeup says outright that "builds
rely on external resources… so images built at different times may not be
identical" ([epoch.ai/blog/swebench-docker](https://epoch.ai/blog/swebench-docker)),
and neither the [harness reference](https://www.swebench.com/SWE-bench/reference/harness/)
nor the [Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
mentions digests at all. Harbor's `task.toml` carries `docker_image` by name and
tag with no digest, checksum or task-id field
([harborframework.com/docs/tasks](https://www.harborframework.com/docs/tasks)).
Inspect's `eval_set` identifies a task by **file path plus function name**
(`ctf.py@jeopardy`) and the only hash in its `.eval` recorder is an **S3 ETag
used for `If-Match` concurrency control** — optimistic locking, not integrity
([eval-sets](https://inspect.aisi.org.uk/eval-sets.html)). Prime Intellect pins
`owner/name@0.1.3`, an author-declared semver with no lockfile. OpenEnv resolves
`from_hub("org/env")` and `--tag env:latest`, both mutable
([huggingface.co/docs/openenv](https://huggingface.co/docs/openenv/index)). ATIF
has `session_id`, `trajectory_id`, `tool_call_id` — all explicit, none derived —
and the trajectory does not reference the task or image by version *or* digest
([ATIF](https://www.harborframework.com/docs/agents/trajectory-format)). METR's
`task-standard` has been untouched since 2025-02-03 and METR itself migrated to
Inspect ([vivaria README](https://github.com/METR/vivaria)); its bridge invokes
images like `inspect-tasks:blackbox-1.0.2`, a tag.

The consequence is concrete and has a price tag. **Terminal-Bench 2.1 patched 28
of 89 tasks**, and the first category in its own announcement reads: "Terminal-Bench
2.0 pinned pre-built Docker images for reproducibility, but internet access
introduces external dependencies that can change over time"
([2.1 announcement](https://www.tbench.ai/news/terminal-bench-2-1)). Repairing
them moved Claude Code + Opus 4.6 by **+12.1%** overall, with per-task swings from
**+84.3%** to **−18.6%**. Under content addressing that drift is not a discovery
six months later; it is a hash mismatch at load time.

**(3) The `not_applicable` verdict is genuinely unoccupied, and the evidence that
it matters is unusually direct.** Eight of Terminal-Bench 2.0's 89 tasks had
resource budgets under which *even the oracle solution* could not reliably
finish — recorded as capability failures for six months. The Agentic Benchmark
Checklist ([arXiv:2507.02825](https://arxiv.org/abs/2507.02825), UIUC-led, 25
authors including Percy Liang and Ion Stoica) records that **τ-bench counts empty
responses as successful** and that SWE-bench Verified uses insufficient test
cases. OpenAI's retirement audit judged **59.4% of the tasks it examined broken**
— 35.5% requiring function names never stated in the problem, 18.8% testing
features outside it — every one of which was scored as a model failure. *(That
audit is quoted from [Decrypt](https://decrypt.co/359012/openai-benchmark-measure-ai-coding-supremacy-contaminated)
and [blockchain.news](https://blockchain.news/news/openai-abandons-swe-bench-verified-contamination-flawed-tests),
which agree; [OpenAI's own page](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
returns 403 and I did not read it.)* Meanwhile ORS — the standard this repo
names in its own README — defines a reward as an **optional float on a
`ToolOutput`**, with no rubric, no vector, no verifier contract and no abstain
state ([openrewardstandard.io/concepts/rewards](https://openrewardstandard.io/concepts/rewards.md)).
Harbor's is `reward.json` or `reward.txt`. Neither can represent "this could not
be judged," so both encode it as zero.

The nearest prior art anyone has published is one line in one single-author
preprint: a verifier-fuzzing study that reports `jsonschema` as *zero coverage /
format-level non-engagement* rather than as a 0.000 false-positive rate, and
reports **N/A** for false-negative rate when no correct candidates exist
([arXiv:2606.01066](https://arxiv.org/abs/2606.01066)). That is the entire
literature. The credible-lab formalization of noisy verifiers — Sugiyama et al.,
verifier unreliability as a stochastic channel with asymmetric rates (ρ₀, ρ₁)
([arXiv:2510.00915](https://arxiv.org/abs/2510.00915)) — **has no third symbol**.
TRAAVIIS has had the four-state vocabulary since its first identity commit.

**(4) The cheating is at the harness level, not only the model level, and it is
an ambient-authority problem.** A UPenn group (Stein, Brown, Hassani, Naik, Wong)
audited thousands of runs across nine benchmarks and found harness-level cheating
in top leaderboard submissions: one Terminal-Bench 2 entry read from a `/tests`
directory in **415 of 429 traces**; another injected `AGENTS.md` answer keys,
whose removal moved it **from 1st place to 14th**
([debugml.github.io/cheating-agents](https://debugml.github.io/cheating-agents/),
[arXiv:2604.11806](https://arxiv.org/abs/2604.11806)). Separately, SWE-bench
containers ship the full `.git` directory, so the fix commit is physically
present ([SWE-bench issue #465](https://github.com/SWE-bench/SWE-bench/issues/465));
Cursor measured that sealing history and network moved Opus 4.8 Max from
**87.1% → 73.0%** and Composer 2.5 from **74.7% → 54.0%**, with **63% of one
model's successful resolutions retrieving the fix rather than deriving it**
([cursor.com/blog/reward-hacking-coding-benchmarks](https://cursor.com/blog/reward-hacking-coding-benchmarks));
Poolside saw a ~20-point overnight jump on SWE-bench Pro from the same class of
exploit ([poolside.ai/blog/through-the-looking-glass](https://poolside.ai/blog/through-the-looking-glass)).

TRAAVIIS's Residency snapshot is an explicit hashed file list with declared
exclusions, so "the answer is reachable from inside the subject" is a **property
of `snap-…`** rather than something a security researcher rediscovers per
benchmark. That is a real structural advantage and it should be said out loud.
But the network half is not covered, and §0 records why: the runner declares
`network: unrestricted` because that is the truth.

**(5) The commercial framing exists and is not ours.** RL environments are a real
market with real money — Anthropic reportedly discussing >$1B, Mercor at $10B,
Prime Intellect at $130M/$1B with ~$100M annualized
([TechCrunch](https://techcrunch.com/2025/09/21/silicon-valley-bets-big-on-environments-to-train-ai-agents/),
[SiliconANGLE](https://siliconangle.com/2026/07/08/prime-intellect-raises-130m-1b-valuation-ai-training-platform/))
— and it is a market where the buyer cannot tell which of the twenty things they
bought worked. Nathan Lambert, February 2026: *"labs will buy 10-20 environments
for millions of dollars and get benefit out of a few of them"*
([note](https://substack.com/@natolambert/note/c-215530896)). Epoch's read is
that "maintaining quality while scaling is the number one bottleneck"
([epoch.ai/gradient-updates/state-of-rl-envs](https://epoch.ai/gradient-updates/state-of-rl-envs)).
A July 2026 procurement review names the failure mode as **"silent drift; no
visible failure"** with no mature standard for maintaining environment fidelity
([future-stack-reviews.com](https://future-stack-reviews.com/rl-environment-platforms/)).

That is the audience. It is *not* frontier-lab RL infrastructure teams — they
have artifact stores and will build this internally. It is third-party
evaluators, auditors, and environment vendors who need to hand a buyer a
statement of what changed. Hugging Face's `Repo2RLEnv` already content-addresses
tasks and images ([README](https://github.com/huggingface/Repo2RLEnv)) — as a
cache key, not as a stated correctness property, but it exists, and "we do it on
purpose and they do it by accident" is a thin thing to sell on its own.

**The conclusion I draw, and it is a demotion of the current pitch:**
content-addressed identity alone is a feature, not a wedge. A perfectly
content-addressed environment with a 28%-hackable test suite trains a reward
hacker exactly as reliably as a drifting one. The pair **identity + honest
verifier coverage** is the defensible position, and *coverage is the half that
carries the weight* — because it is the half nobody has and the half that changes
a reported number.

---

## 2. Candidates

Twelve, argued individually. Cost is stated in this repo's terms: new modules,
new laws, new rungs, and above all **whether it moves an id**, which is the
expensive thing here.

---

### A. One end-to-end pass a stranger can verify

**What it is.** A single reproducible demonstration that goes WRLM → WRL → TRVM →
TRAAVIIS in one pass, packaged so that somebody who has never seen the repository
can run it and check its own claims. Concretely: a WRLM-proposed goal lowered to
a WRL source, sealed by Forge to a `sem-…`, folded by TRVM, admitted by TRAAVIIS
into an `env-…`/`bundle-…`, evaluated to an `episode-…`, and re-verified by
`trvs verify-episode` — with every intermediate id printed and pinned, and a
single script that reproduces the whole thing from a clean checkout and asserts
the pinned ids.

**For.** `STACK_COMPLETION.md` records the gap in its own words: *"the
WRL/TRVM/WRLM/TRAAVIIS chain has never been exercised end-to-end as a single pass
by an outside consumer."* Four repositories are organized around a claim
(*WRLM proposes → WRL seals → TRVM reduces → TRAAVIIS admits*) that has never
been executed as a claim. Every one of the four has a green battery; none of them
has evidence that the *composition* works, and a battery that passes in four
repositories separately is precisely the evidence a layering error survives. This
item also converts every other item on this list from an assertion into something
with a demonstration attached, and it is the only item that produces an artifact
the audience identified in §1(5) can actually be handed.

**Against.** It is a demonstration, not a capability — it ships no new power, and
a roadmap that leads with a demo can be a roadmap avoiding a hard decision.
It is also the item most likely to find that the seams do not fit, which will
generate work in three repositories that are not this one, on a schedule this one
does not control. And there is a real scope trap: "end to end" can silently mean
"and therefore WRLM steps 3–10," which are paper only. The demo must be pinned to
the WRLM that exists (`GoalSpecV1` + `TaskBundleV1` + `envelope`/`coverage`), not
to the WRLM that is designed.

**Cost.** No new module in `traaviis/`. One script, probably in `tools/`, and one
new example directory. No new law in the sense the batteries use, but the script
*is* a law — it should assert pinned ids and exit non-zero on drift, i.e. behave
like `replay --expect`. **No id moves.** The cost is coordination across four
repositories, not code.

**Unblocks.** Every claim in §1 that begins "TRAAVIIS can…". Also the honest
prerequisite for D (the export adapter) and for any outside consumer at all.

---

### B. Audit `canonical_bytes` against RFC 8785, and forbid floats

**What it is.** `identity.canonical_bytes` is `json.dumps(sort_keys=True,
separators=(",",":"), ensure_ascii=False)`. **RFC 8785 (JCS)** specifies exactly
this shape with two differences that matter: object keys are sorted
**lexicographically by UTF-16 code unit**, where Python sorts by code *point*
(these differ for non-BMP keys, because surrogates sort below U+E000–U+FFFF); and
numbers are serialized per ECMAScript §7.1.12.1 with its shortest-round-trip
rule, where Python's `repr` is close but not specified to be identical
([RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html)). The work is: determine
whether any live id can contain a non-BMP key or a float; if not, **declare
conformance and add a law that keeps it true**; if so, decide (see §6).

**For.** "Our ids are SHA-256 over RFC 8785 canonical JSON" is a one-sentence
specification that any consumer can implement from an off-the-shelf library in
Go, Rust, JavaScript or Java. "Our ids are SHA-256 over our canonicalizer" is a
reimplementation project for every consumer, and it silently makes TRAAVIIS the
only correct implementation of its own format — which is exactly the position
this repository refuses everywhere else. The whole thesis is "you do not have to
trust me"; an unspecified hash input is a place where you do. It is also cheapest
now: ids are frozen keys, the corpus is small, and this check gets more expensive
every month it is not done.

**Against.** If the audit finds a divergence that matters, the fix moves ids, and
moving an id here is the most expensive thing on this list — every recorded
`episode-…`, `env-…` and `bundle-…` in the examples, any packet already built and
accepted, and every pinned literal in the batteries (K27's
`PRE_LINEARIZATION_EPISODE_ID` most pointedly) becomes wrong. There is also a
version of this that is pure ceremony: if no id can contain a float or a non-BMP
key, then Python's output *is* JCS output and the entire item is a paragraph in a
doc plus one law.

**Cost.** If conformant: **0 new modules**, one new law in `test_identity.py`
asserting the value domain (no floats, BMP-only keys) and one doc paragraph. No
id moves. If not conformant: a canonicalizer of roughly 40 lines, a migration
decision, and a moved id everywhere. **The audit itself is a few hours and must
happen before anything else in this document is built**, because half the items
below quote ids.

Note the float question is not hypothetical. `RewardSpecV1` weights and
`reward_max` are floats and they enter `rew-…` via `canonicalize_reward`; the
scaffolded specs use values like `0.25` and `0.30`, which are exactly
representable enough in practice that `repr` agrees with ECMAScript today. That
is a fact about the values that ship, not a property of the format.

**Unblocks.** C, E, and any third-party re-derivation of any id.

---

### C. Report coverage as a first-class reading, without folding it into the reward

**What it is.** The wedge from §1(3), made concrete. Today `reward` is a single
float and a `not_applicable` optional signal is arithmetically indistinguishable
from a `fail`. The fix is *not* to change the arithmetic — the arithmetic is
ruled and `pass`-contributes-weight is correct — but to report, alongside every
reward, the **coverage denominator**: which declared signals answered, which
declined, and what fraction of the rubric's total weight was actually in play. So
a receipt reading `reward 0.6` becomes `reward 0.6 · coverage 0.75 (identity
not_applicable, weight 0.15 unscored)`, and `trvs eval` / `batch` / `compare`
print and aggregate it.

The critical design point: **this needs no new field in the identity allowlist.**
Coverage is a pure function of `verification`, the `rew-…` signal map, and the
task's `verifier_plan.required` — all three already sealed inside `episode-…`. It
is a *reading* of sealed bytes, in exactly the sense `ComparisonV1` is a reading
of two sealed episodes and correctly mints no id. Adding a `coverage` field to
`_EPISODE_IDENTITY_KEYS` would move every episode id to record something already
derivable from them, which is the same mistake as a `compare-…` rung.

**For.** This is the one thing on the list nobody else has and the one thing that
changes a *number* rather than adding a *guarantee*. §1(3) is six independent
pieces of evidence that the field is currently mis-scoring unanswerable cases,
and the total published prior art for "the verifier declares itself inapplicable"
is one line in one single-author preprint. It composes with the credible academic
frame (Sugiyama et al.'s (ρ₀, ρ₁) channel has no third symbol; adding
not-applicable mass to it is a defensible extension with a named target). And it
is *cheap*, because the vocabulary has been in `reward.py` since the first commit
— TRAAVIIS is not adding a concept, it is surfacing one it already had and then
threw away at the last step.

**Against.** The honest scope limit has to be stated in the same breath, or this
gets dismissed: coverage changes the **report**, not the trained model. The
strongest counter-evidence available is a preregistered study of a leaky reward
suite with a 15.30% static false-positive rate that produced a held-out gap of
only **0.20 points** at step 400 ([arXiv:2607.11022](https://arxiv.org/abs/2607.11022),
single-author). Anyone credible will ask whether this moves a policy or only a
spreadsheet, and if the answer is "a spreadsheet," saying so is the only version
of this that survives contact. Also: three of the four most quotable 2026
verifier-reliability results are single-author preprints with no independent
replication, so the argument should rest on ABC (arXiv:2507.02825, 25 authors)
and Sugiyama et al., with the preprints as colour.

**Cost.** One small module or one function in `reward.py` (`coverage()`),
returning `{answered, declined, required_unanswered, scored_weight,
declared_weight}`. Roughly 5–8 new laws: coverage is total over declared signals;
a `not_applicable` optional signal lowers coverage and not validity; an `error`
episode reports coverage `None` rather than 0 (mirroring the reward rule);
coverage is derivable from a sealed episode with no other input; and — the
load-bearing one — **computing coverage moves no `episode-…`**, asserted by
re-deriving every example id before and after. Printing surfaces in `eval-one`,
`eval`, `verify-episode`, `compare`, `batch`. **No id moves, no schema
migration.**

**Unblocks.** The only genuinely novel claim TRAAVIIS can make in 2026. Also a
short paper or a NIST AI 800-2 comment (§6, decision 5).

---

### D. Publish: claim the name, ship an installable package, fix the README

**What it is.** `pip install traaviis` 404s. The README opens with an install
command that cannot work. `tools/build_packet.py` + `tools/accept_packet.py`
already produce and gate a release packet — extract into an empty directory, run
the battery with and without the engine, rebuild with two extractors to prove the
hash is host-independent — which is a stronger release gate than most published
packages have. It is just never published. This item is: claim the PyPI name, cut
`0.1.0`, publish the acceptance-gated packet as a GitHub release, and correct the
README to describe what a reader can actually do today.

**For.** The README currently makes a claim that fails on the first line, which
is the one failure mode this repository's culture is organized against. It is
also the precondition for literally every consumer: nobody evaluates an
evaluation tool they cannot install. And the gate already exists — this is
publishing work that has been done, not doing work.

**Against.** Publishing a `0.1.0` that requires a Forge/TRVM engine at an
unpublished path for six of its seventeen shipped commands (`id`, `inspect`,
`run`, `verify`, `replay`, `diff`) ships a package that half-works
for anyone who is not Travis. The Residency substrate does not need Forge (the
`pyproject` comment says so) but the world commands do, and `trvs doctor` will
tell a new user that their install is broken through no fault of their own. The
honest sequencing is: either publish with the world commands documented as
requiring an engine you must obtain separately, or publish Residency-only and
say so. Neither is free.

**Cost.** No modules, no laws, no id moves. A `README` correction, a PyPI
account, one release. The real cost is the decision in §6 about whether the
repository goes public.

**Unblocks.** E, F, G, and every external consumer.

---

### E. An in-toto attestation adapter (`trvs attest`)

**What it is.** Emit an [in-toto Attestation Framework v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
`Statement` over an episode or a bundle:

```json
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "episode-…", "digest": {"sha256": "…"}}],
 "predicateType": "https://traaviis.com/episode/v1",
 "predicate": { … the rungs, the verification map, the coverage … }}
```

Custom predicate types require **no registration and no tooling buy-in** — the
spec says users may "develop a new one if no existing one satisfies," and MAY
have it vetted. Multi-attestation bundles are JSON Lines (`.intoto.jsonl`). The
signing half stays out of the package: Python's stdlib has no asymmetric
signing, so DSSE would mean a dependency. Instead, a public repository can use
[`actions/attest`](https://github.com/actions/attest) with a custom
`predicate-type`, which routes to the Sigstore public-good instance for public
repos, is **free on all current GitHub plans**, and yields `gh attestation
verify`.

**For.** It makes TRAAVIIS's ids legible to an entire existing ecosystem —
cosign, `gh attestation verify`, Sigstore policy controllers, GUAC — without
anyone adopting the ladder. And per §1, it closes the one gap deterministic
replay structurally cannot: **replay proves consistency, not that the original
run happened when it was claimed.** A free transparency-log entry is the only
thing on this list that turns "I hashed it" into "someone else can check I did
not backdate it." The [existing `Test Result` predicate](https://github.com/in-toto/attestation/tree/main/spec/predicates)
should be read before minting a new type; an episode is arguably one.

**Against.** Emitting an attestation nobody consumes is a JSON file with a URI in
it. The honest question is who verifies it, and today the answer is nobody,
because of D. This is also the item most at risk of being *nearly* right: SLSA
provenance looks like an uncanny structural fit (`resolvedDependencies` ≈ the
inputs, `byproducts` ≈ the trace, `subject` ≈ the bundle) and is **semantically
wrong** — SLSA asserts that a hardened build platform produced an artifact, and a
local `trvs eval-one` is Build L1 at best. Claiming it would invite readers to
infer a build-integrity guarantee TRAAVIIS is not making. Use the vocabulary,
never the predicate type.

**Cost.** One module, ~100 lines, zero dependencies, one CLI verb. Roughly 6
laws: the Statement round-trips; the subject digest equals the id's own hash; the
predicate is derivable from the sealed bundle alone; **emitting an attestation
moves no id** (it is a reading, like `compare`); an unsigned Statement never
claims signature; the predicate type is versioned. **No id moves.** ~15 lines of
CI YAML for the signing half, and zero Python.

**Unblocks.** Third-party verification without third-party re-execution — which
§1(1) argues is the thing the field just lost.

---

### F. Adopt somebody else's canonical serialization for distribution: OCI + ORAS

**What it is.** `trvs archive-bundle` already emits a canonical ZIP with a
transport checksum. Distribution today is "email someone a zip." OCI artifacts
via `oras push` to `ghcr.io` give digest-addressed, CDN-backed, resumable
distribution from infrastructure that already exists, at **$0 for public
packages**. `artifactType` marks a manifest as a non-image artifact and the
`subject` field creates a referrer association, so an attestation from E can be
`oras attach`ed to a bundle.

**For.** It is a documented shell recipe, not code — the whole thing is one
`oras push` line plus a paragraph in the README, and it composes with E because
`cosign verify-attestation` and `gh attestation verify` both speak OCI digests
natively.

**Against.** It solves a problem TRAAVIIS does not have yet, because nobody is
downloading bundles. It is also the kind of item that reads as progress while
moving no argument. Do it when someone asks where to get a bundle.

**Cost.** Zero code, zero laws, zero id movement, ~20 lines of documentation.

---

### G. The drift audit — the study that could falsify the thesis

**What it is.** Take N environments from a public hub (Prime Intellect's, or
Harbor Hub's `terminal-bench/terminal-bench-2-1` with its integer revision
suffix), pin the declared version, re-resolve them over time, and count how many
cases the **declared version was stable while the resolved content was not**.
Publish the number, including if it is zero.

**For.** This is the empirical result that turns "author-declared semver is
insufficient" from a design opinion into a measurement, and nobody has published
it. The field has published the *consequence* — Terminal-Bench 2.1's 9 tasks with
internet-introduced drift, moving one agent 12.1% — but not the *rate*. It is
also the single cheapest way to find out whether the identity half of the pitch
is worth defending, and the culture here says you check before you build on a
claim. If the number is near zero, that is a finding, and it should change this
roadmap.

**Against.** It is research, not product; it consumes time TRAAVIIS could spend
being usable; and it may require accounts or bandwidth. The Prime Intellect
dashboard is auth-walled (it was not enumerable in this pass), which may make the
largest catalog the hardest to sample.

**Cost.** No package code. A script and a writeup. It generates no laws and moves
no ids. The cost is time and possibly a modest amount of bandwidth.

**Unblocks.** Whether to keep leading with identity at all.

---

### H. A real isolation posture (`network: disabled`)

**What it is.** A second runner profile that actually restricts the network
(unshare/netns on Linux, or a documented refusal on other platforms), so a task
can *declare* `network: "disabled"` and have `runner.py` accept the declaration
because it is true.

**For.** §1(4) is the dominant measured failure mode in agent evaluation right
now, and its network half — 63% of one model's SWE-bench Pro passes retrieving
rather than deriving, a 14–21 point swing from sealing history and network — is
the half TRAAVIIS cannot currently touch. A repository-evidence substrate whose
subject is content-addressed but whose agent can `curl` the upstream fix has
sealed the wrong thing.

**Against.** This is a sandbox, and "not a sandbox" is currently an *honest*
statement rather than a missing feature — the runner refuses to seal a posture
it does not deliver, which is better than every system surveyed in §1. Building a
half-sandbox is strictly worse than declaring no sandbox, because the label would
then be a claim, and a claim is what gets discovered by a retry loop at 3am. It
is also plausibly **somebody else's layer**: the README says TRAAVIIS is not an
RL cloud and does not host; process isolation is what Docker/Harbor/Inspect's
eight sandbox providers are for. The narrow version — a Linux-only network
namespace with an explicit `not_applicable` on every other platform, and the
platform recorded in `execution_facts` so the episode says which — is defensible;
anything broader is a different product.

**Cost.** One module, one new runner profile, a change to `execfacts` and to the
policy negotiation in `runner.py`. **This moves ids**: the runner profile enters
`execution_facts`, which is inside `_EPISODE_IDENTITY_KEYS`, so any episode run
under the new profile is a new id. That is *correct* — a different isolation
posture is a different episode — but it means the profile must be additive and
existing episodes must be re-derivable, which needs a law.

**Unblocks.** The only credible answer to "does TRAAVIIS stop the cheating in
§1(4)?" Today the answer is "half of it."

---

### I. An export adapter to somebody else's catalog

**What it is.** The README's stated strategy is explicit: *"do not compete with
hosting catalogs. TRAAVIIS aims to be one of the best ways to author
deterministic environments that export to them."* Nothing exports. The candidates
are Harbor (`task.toml` + `environment/Dockerfile` + `tests/test.sh`, feeding
Terminal-Bench), ORS (the standard already named in the README, and which
`serve --ors` already speaks — so this is an *authoring* export, not a transport),
and OpenEnv (`openenv.yaml` + `uv.lock` + Dockerfile, publishable to an HF Space).

**For.** It is the stated strategy, unexecuted. It is also the only item that
puts a TRAAVIIS-authored environment in front of somebody who is not looking for
TRAAVIIS. And there is a specific asymmetry worth exploiting: exporting *into* a
tag-addressed catalog while retaining the `env-…`/`bundle-…` that the catalog
cannot express means TRAAVIIS becomes the thing that can tell you the catalog
drifted.

**Against.** Picking a catalog is picking an ecosystem, and all three are moving.
`verifiers` was rewritten in July 2026 and its pre-1.0 abstractions are legacy
([v1 announcement](https://www.primeintellect.ai/blog/verifiers-v1)). ORS has
**no version number anywhere in its specification**, no conformance suite, no
compatibility policy, and its GitHub org contains exactly one repository (a
Python SDK, 4 commits, last touched 2026-03-24) — there is no spec repo, so it is
a docs site rather than a versioned artifact, and its governance is one company's.
OpenEnv moved from Meta to Hugging Face with no announcement I could find, and
its org has 15 public Spaces. Building an adapter now is building against a
moving target with a real chance of being obsolete in two quarters. Harbor is the
most stable of the three and has actual review discipline (~3 reviewer-hours per
task, an adversarial exploit agent pre-merge).

**Cost.** One module per target, ~200–400 lines, a template, and a battery
asserting the round trip is lossy *in a named direction* (a `bundle-…` cannot
survive into a `task.toml`, and the adapter must say so rather than pretend).
No id moves.

**Unblocks.** External users. Also G, in reverse — an import direction would let
TRAAVIIS content-address somebody else's environment, which is a more interesting
product than exporting into theirs.

---

### J. The deferred `eval-…` and `agent-…` rungs

**What it is.** Two identity rungs deliberately not built. `eval-…` would give a
split-run an id of its own; `agent-…` would give a candidate an identity beyond a
report label.

**What breaks today because they do not exist.** Honestly: less than the deferral
list implies, and the specific breakages are worth naming rather than gesturing
at.

*Without `eval-…`:* an `EvaluationV1` index is not addressable. You can hand
someone a directory of episodes and a JSON index, and every episode is
independently verifiable, but there is no single string that means "this exact
run over this exact split." The concrete consequence is in `batch`: `batch.json`
and `comparisons/` are the artifact a reader is actually handed, and they carry
no identity, so two batch outputs that differ can only be compared by diffing
trees. The RFC says distribution identity for batch output "is deferred and needs
a separate ruling." That is the real gap, and it is narrow — it bites exactly
when someone wants to cite a *run* rather than an *episode*.

*Without `agent-…`:* nothing breaks, and building it would break something. The
batch memo is explicit and correct: a `candidate_key` is a local report label,
rename every candidate and every `episode-`, `task-`, `trace-` and `env-` is
byte-identical, because the mode rides in `argv`. An `agent-…` rung would have to
seal *something* about the agent, and the only honest candidates are the argv
(which is a host-coupled string, the exact thing test-plan v2 removed from
`task-…`) or the model identity (which TRAAVIIS explicitly does not know — "not a
model router" is a frozen boundary). **An `agent-…` rung would require TRAAVIIS
to have an opinion about what an agent is, and it deliberately does not.** My
read is that this rung should be *closed* rather than deferred, and the reason
written down.

**For (`eval-…` only).** It makes a run citeable. Combined with E, an
attestation over a run is the shape a third-party evaluator actually wants —
"here is a signed statement that this agent scored this on this split," where the
split is a hash.

**Against.** Every rung added is a rung that must hold forever, and the ladder's
current stopping point is a considered ruling, not an oversight: `eval` "emits no
new artifact id — the ladder stops at `env-…`". Adding `eval-…` requires deciding
what is *outside* it (wall-clock, host, output paths, ordering) and getting that
wrong is unrecoverable. And it is not obviously needed before somebody asks for
it.

**Cost.** `eval-…`: one identity function, an allowlist decision, ~8 mutation
laws, an `EvaluationV2` (which the RFC already anticipates, renaming the legacy
`bundle` field to `episode_member`). **This does not move existing ids** if
`EvaluationV1` is left alone and V2 is additive — but it is a schema migration
for anything reading the index. `agent-…`: recommend closing, cost zero.

---

### K. Auth, TLS, session quotas

**What it is.** The current answer is loopback plus a blunt `--allow-remote`.

**Is loopback-only a feature?** Given the threat model, largely yes, and the
memos already argue it well: the server holds a candidate's patches and runs
verifier commands, so "reachable from the network" is not a sane default, and it
is a flag rather than an inference from the address so exposing it is something a
human typed. Read against §1(4) it is stronger than that — a submission endpoint
reachable from the internet, running arbitrary test commands under a runner that
declares `network: unrestricted`, is a remote code execution service with a
scoreboard.

**The smallest honest step beyond it** is not TLS and it is not OAuth. It is:

1. A **shared-secret bearer token**, required whenever `--allow-remote` is
   passed, generated by the server if not supplied and printed once to stderr.
   Roughly 30 lines with `hmac.compare_digest`. This raises the bar from "anyone
   who can route to the port" to "anyone who was told the token," which is the
   entire distance most of the risk lives in.
2. A **per-session concurrency and body cap** — the body cap already exists at
   8 MiB; a cap on simultaneously-scoring sessions is the missing half, because
   the expensive operation is running a candidate's test suite and nothing limits
   how many of those a client can start.
3. **TLS: no.** Terminating TLS in `http.server` correctly is a dependency-shaped
   problem with a certificate-shaped problem behind it, and the right answer for
   a zero-budget maintainer is a documented reverse proxy — which is not code in
   this repository.

**Against the whole item.** Nobody is running this remotely. Every line of it is
speculative until someone does, and a token nobody uses is a token that rots.
The strongest version of this item is **documentation**: state the threat model
in the README, say that `--allow-remote` without a proxy is not a supported
configuration, and build the token when the first person asks.

**Cost.** Token: one module, ~40 lines, 4 laws (a remote bind without a token is
refused; the token is compared in constant time; the token never appears in a
response or a log line; a keyless request is 401 not 404). No id moves.

---

### L. A stateful substrate (Courier), to exercise `step` / `observe`

**What it is.** `EpisodeKernelV1` ships seven verbs and Residency implements
four; `observe`, `step` and `reset` exist only to be refused. The kernel was
extracted before a transport existed, which is the right order — but it has never
been exercised by a substrate that needs the verbs it was designed around.

**For.** A kernel whose interactive half has never run is a design, not a
contract. `ARCHITECTURE.md` §4 already sequences Courier at v0.2 *specifically*
"so the kernel is exercised by a stateful, long-horizon environment before batch
`eval` is called complete" — and batch `eval` shipped anyway. That is a
sequencing commitment the tree has already quietly broken, and it should be
recorded as such rather than re-planned around.

**Against.** It is the largest item on the list by a wide margin — a world, its
verbs, a reward vocabulary for long-horizon tasks, a trace format for multi-step
behaviour, and probably process rewards behind it. It also aims at the part of
the market that is most crowded and best funded (§1(5)). And the current
one-shot substrate is the one that maps onto the *evidence* thesis; a stateful
world is a different product with a different claim.

**Cost.** Multiple modules, a new substrate profile, a large battery, new
observable-record semantics. **No existing id moves** (a new substrate is
additive, which is the point of the substrate admission interface) but the surface
area is measured in weeks, not days.

---

## 3. The four questions this brief posed

**Is a single reproducible end-to-end demonstration worth more than any new
feature?** Yes, and the argument is not sentiment about demos. TRAAVIIS's entire
claim is *checkability by a stranger*. Every other item on this list adds
something a stranger cannot currently check, because there is no stranger — no
published package, no public artifact, no run they can reproduce. A repository
whose culture is "a claim must be checkable" currently has one unchecked claim at
its foundation: that the four layers compose. Building feature thirteen on top of
an untested composition is the specific failure mode this repository writes
memos about. The counter-argument is real and should be weighed: a demo is not a
capability, and item C is the only genuinely novel thing on the list, and doing A
first delays it. My answer is that A and C are not competing, because C's output
*is* what A should be demonstrating — an episode that reports a reward and a
coverage denominator is a far better demonstration than one that reports a
reward. Do A with C inside it.

**Is deployment the bottleneck, or is a downloadable self-verifying packet the
right distribution model?** The packet is right, and this is not a budget
rationalization. A hosted TRAAVIIS is a server whose answers you must trust,
which contradicts the product. `verify-episode` replays an episode with no agent
and no server, which is a stronger statement than any endpoint can make. §1(1)
supports this from the outside: the field's verification mechanism was
re-execution by a trusted third party, and it just archived, because it costs
tens of thousands of dollars per study. A self-verifying packet moves that cost
to zero and removes the trusted party.

Two honest qualifications. First, "self-verifying" is not quite true today:
`verify_episode_bundle` re-runs the declared verifiers, so replay needs the same
*toolchain* even though it needs no agent and no server. A stranger with no
`pytest` cannot verify a Residency episode. That limit should be written down and
`verify-episode` should say which verifiers it could not run — which is item C's
vocabulary applied to replay. Second, the packet is not deployed either: **the
bottleneck is not deployment, it is publication** (item D), which is a different
and much cheaper thing. "Nothing is deployed" and "nothing is downloadable" have
been read as one problem and they are not.

**What breaks today because `eval-…` and `agent-…` do not exist?** Answered at
length in J. Short version: `agent-…` breaks nothing and would break the model
boundary if built — recommend closing it, not deferring it. `eval-…` costs
exactly one thing: a batch or split run is not citeable as a unit, so `batch.json`
and the comparison tree are handed to a reader with no identity on them. That is
narrow, real, and not urgent.

**Is loopback-only a feature?** Yes. Answered in K, with the smallest honest step
being a shared-secret bearer token required under `--allow-remote`, a
concurrent-session cap, and an explicit refusal to terminate TLS in-process.

---

## 4. The ranked list

**Ranking criterion, stated so it can be argued with: prefer the item that makes
an existing claim checkable by a stranger over the item that adds a new claim.**
Ties broken by whether the item is cheaper now than later. This criterion
demotes every feature and promotes an audit and a demo, which is uncomfortable
and is the point — the repository already has more capability than evidence.

| # | item | why here |
| --- | --- | --- |
| 1 | **B — audit `canonical_bytes` against RFC 8785** | Strictly cheapest now; every id is a frozen key and the corpus is at its smallest today. May be a no-op, and finding that out is worth a day. Everything below quotes ids. |
| 2 | **A — one end-to-end pass a stranger can verify** | The foundational unchecked claim. Also the artifact the §1(5) audience can be handed. Include C's coverage line in its output. |
| 3 | **C — coverage as a first-class reading** | The only genuinely novel claim available in 2026, cheap, moves no ids, and the vocabulary already exists in `reward.py`. Third only because A gives it something to be reported *in*. |
| 4 | **D — publish** | The README's first line does not work. Fixing a false claim outranks adding a true one. |
| 5 | **G — the drift audit** | Could falsify the identity half of the pitch. Do it before building more on that half, not after. |
| 6 | **E — in-toto attestation** | Closes the one gap replay cannot (time), free, ~100 lines, no id movement. Ranked below D because it needs a consumer. |
| 7 | **H — a real network-isolation posture (Linux-only, narrow)** | Addresses the dominant measured failure mode. Ranked here and not higher because the current honesty is worth more than a half-sandbox, and because it moves ids. |
| 8 | **K (documentation half) — write down the threat model** | Free. The token half waits for a user. |
| 9 | **I — one export adapter, Harbor first** | The stated strategy, unexecuted; ranked below because all three targets are moving and Harbor is the only one with review discipline. |
| 10 | **J (`eval-…` only) — a citeable run** | Real but narrow. `agent-…` should be **closed**, not deferred. |
| 11 | **F — OCI/ORAS distribution** | Zero code, but solves a problem that does not exist until D lands. |
| 12 | **L — Courier / a stateful substrate** | The largest item, aimed at the most crowded market, and the kernel's interactive half can wait for a substrate that needs it. |

---

## 5. Not doing, and why

This section is load-bearing. Several of these are things a reasonable person
would put on a roadmap, and the reason they are not here is that the README's
"What TRAAVIIS is not" list is strict on purpose.

**A sandbox runtime.** Docker, Kubernetes, microVMs, the eight-provider sandbox
matrix Inspect ships. This is somebody else's layer and the README says so ("not
an RL cloud"). The narrow Linux-netns item (H) is included precisely because it
is *not* this — it is one honest posture, not a runtime. Anything that requires
maintaining container images is out.

**An `agent-…` rung.** Not deferred — **closed**, with the reason recorded. It
would require TRAAVIIS to have an opinion about what an agent is, and "not a
model router" is a frozen boundary. The only sealable candidates are a
host-coupled argv (which test-plan v2 deliberately removed from `task-…`) or a
model identity TRAAVIIS refuses to know.

**A benchmark corpus.** 500 SWE-bench Verified instances, 89 Terminal-Bench
tasks at ~3 reviewer-hours each, 2,500 claimed Hub environments. One person with
no budget does not out-author that, and trying converts a tools project into a
content project with a permanent maintenance tail.

**A leaderboard.** HAL was the good version of this and it archived after
~$40,000 of rollouts. The whole argument in §1(1) is that content addressing
lets you *avoid* being the trusted re-executor.

**SLSA provenance for episodes.** Structurally uncanny, semantically wrong. SLSA
asserts a hardened build platform produced an artifact; a local `trvs eval-one`
is Build L1 at best. Emitting it would invite a guarantee TRAAVIIS is not making.
*(CI may legitimately emit SLSA provenance for the released package — a different
artifact and a different claim.)*

**C2PA.** Wrong domain (media assets), and participation now requires an X.509
certificate on the C2PA trust list, whose interim list froze 2026-01-01 — a paid
CA path, which the budget forecloses.

**TEE attestation and zkML.** Attestable Audits ([arXiv:2506.23706](https://arxiv.org/pdf/2506.23706))
is the closest prior art and it is a research prototype on AWS Nitro; the largest
model proven end-to-end in ZK is roughly GPT-2 scale. Both require cloud spend.
There is a *conceptual* point worth putting in the docs, though: a TEE proves
*this ran, on this code, on this hardware, untampered*, and needs hardware and an
account; TRAAVIIS proves *given these inputs this output follows, checkable
offline for free*. Those are different claims and the difference is the product.

**IPFS, Bazel CAS, CBOR/CDE.** IPFS and Bazel's CAS solve distribution and build
caching, neither of which is a problem here. CBOR/CDE is not an RFC yet and would
cost the `json` module. *(A CIDv1 encoder is ~25 lines and is a pure re-encoding
of the SHA-256 already computed — harmless, optional, and not worth ranking.)*

**Nix as the definition of `env-…`.** A `flake.nix` as *documentation* of what an
environment was is defensible and cheap (~25 lines, inert for non-Nix users). Nix
as the identity is not: it would make the ladder depend on a toolchain the
package does not have, and `ca-derivations` was still experimental and crashing
as of mid-2026.

**A REPL.** Already on the deferred list, already in `ARCHITECTURE.md` §5's
not-built list. Nothing in this pass argues for it.

**An LLM judge.** Terminal-Bench runs one over passing trials and it is the right
call *for them* — they are adjudicating behaviour after the fact across a corpus
they did not seal. TRAAVIIS seals the subject, so the ex-ante manifest does the
work the judge is doing, more cheaply and without requiring a model to be right.
It would also violate "nothing here calls an LLM."

---

## 6. Decisions for Travis, not for an implementer

1. **If the RFC 8785 audit (B) finds a real divergence, do ids move?** This is a
   one-way door. Moving them invalidates every pinned literal in the batteries,
   both packets in `dist/`, and every example episode. Not moving them means
   documenting a permanent, named divergence from the standard and accepting that
   consumers need a TRAAVIIS-specific canonicalizer. Both are defensible; only
   one can be chosen, and it gets more expensive monthly.

2. **Does the repository go public now?** Items D and E's free tiers —
   `actions/attest` keyless signing, the Sigstore public-good instance, free
   GitHub Packages — are **public-repository-only** on current plans. Private and
   internal repositories route to GitHub's private Sigstore instance and require
   Enterprise Cloud. So "publish" and "attest for free" are the same decision.

3. **Does TRAAVIIS ever run a real sandbox (H), or does it stay honest-and-
   unisolated permanently and say so in the README?** This is a product-boundary
   question, not an engineering one. Both answers are respectable; the current
   state — honest label, no isolation, no stated position — is the one that is
   not.

4. **Which catalog, if any (I)?** Picking Harbor, ORS or OpenEnv picks an
   ecosystem, and all three are moving. Choosing none is also a choice and should
   be a stated one, because the README currently promises the export strategy.

5. **Is the coverage work (C) also a publication?** NIST AI 800-2 ipd (*Practices
   for Automated Benchmark Evaluations of Language Models*, released 2026-01-30;
   [announcement](https://www.nist.gov/news-events/news/2026/01/towards-best-practices-automated-benchmark-evaluations),
   [PDF](http://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf)) has a section
   on analyzing and reporting results, and its comment window closed 2026-03-31.
   **Nobody in this pass read that PDF**, and it should be read before any
   publication decision: it either contains the hook or it makes the argument
   redundant. Whether TRAAVIIS's four-state vocabulary is a product feature, a
   paper, or a standards comment is a use-of-time decision above an implementer.

6. **Do the O25/O26 laws get re-scoped by whoever lands `--mcp`, or is that a
   separate task?** It is small, but it is the kind of small that gets closed as
   "expected during the slice" and stays closed. The rule both laws encode still
   holds; only their vantage point is wrong.

---

MIT licensed · TRAAVIIS Holdings · part of the [&] Protocol ecosystem.
