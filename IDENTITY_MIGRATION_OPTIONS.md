# Identity migration options: versioned prefix vs. schema-level scheme vs. never conform

A measured comparison. Written 2026-08-04 against `835caf7` **plus the
uncommitted key-domain work in the tree** (`traaviis/identity.py`,
`test/test_canonical.py`), on which `test/test_canonical.py` reports **39 passed,
0 skipped, 0 failed** and `test/test_identity.py` reports **25/25 passed** — so
every number below was taken from a green tree, and the divergence laws it pins
are currently true. Line references are to the working tree as of that run, and
`test_canonical.py`, `traaviis/scaffold.py`, `test/test_eval_split.py` and
`test/test_compare.py` are all under active edit — prefer a law's `test_cNN_…` /
`test_xNN_…` name over its line number if the two disagree.

~~"**37 passed** … Every measurement here was re-run after that work landed
mid-analysis; none of the numbers moved."~~ **Superseded on both counts.** The
battery is now 39 (C38-C39 landed after this was written), and two numbers did
move: the domain-check count in §4/Option B (two → three, with C18 renamed), and
the whole of §3.4, which was **inverted** by the sibling change to
`scaffold.id_tokens`. Both are corrected in place below, each with the
superseded claim kept beside it. Everything else was re-measured and is
unchanged.

**Because this document supports an open one-way-door decision, treat a number
here as a reading with a date on it rather than as a fact.** Two of the numbers
in the first published version were falsified within a day — one of them by a
recommendation this document itself made being carried out. Re-run the probe
before quoting §3.4.

Everything here that is a count was produced by running something over the
repository, not by reading and estimating. Where a claim is an argument rather
than a measurement it is written as prose and labelled as a judgement. Where a
measurement contradicts something stated in the audit brief that prompted this
document, the measurement is recorded and the brief is corrected.

Scope note: a sibling change closing the canonicalization *input domain* landed
while this analysis was in progress, and then continued after it. It refuses
non-string mapping keys (which previously coerced — `{1: "a"}` and `{"1": "a"}`
used to produce one id) under a new `CANONICAL_KEY_TYPE` code
(`traaviis/identity.py:63`) with laws C31-C37, and refuses strings the encoder
cannot encode (a lone surrogate) under `CANONICAL_ENCODING`
(`traaviis/identity.py:70`) with laws C38-C39. `test_canonical.py` is now **39
passed, 0 skipped, 0 failed**.

~~"adds laws C31-C37 … does **not** change any number in this document."~~
**Superseded, in both halves.** The enumeration was incomplete —
`CANONICAL_ENCODING`, C38 and C39 landed after it was written — and the claim
that no number moved was false as soon as they did: the domain-check count at
§4/Option B was **two** and is now **three**, and C18 was renamed accordingly.
That correction is applied below. The statement is kept because it is the
record of what this document believed when its measurements were taken, and a
reader comparing an older copy needs to see which claim was withdrawn.

What still holds: the sibling work narrows the admissible domain toward I-JSON,
does **not** touch the float divergence, and is not counted as progress on this
decision anywhere below. The §2 and §3 measurements were re-run against it and
are unchanged **except** for §3.4, which was inverted by a different sibling
change and is re-measured there.

---

## 1. The question the reviewer put, answered from the code

The reviewer's framing: a bare SHA-256-shaped id is already not self-describing,
so the real question is whether TRAAVIIS's verification unit is *the identifier
alone* or *the receipt plus the identifier*. If the former, a grammar change is
unavoidable. If the latter, schema-level versioning is the cheaper migration.

**Measured answer: TRAAVIIS is a receipt-plus-identifier system, and there is no
code path anywhere that is not.**

The evidence is a complete enumeration rather than a sample. There are **36 call
sites** of `identity.*_id(...)` in `traaviis/` and `tools/`. Every one of them
names its rung statically in the function it calls, and passes a document whose
type the caller already knows:

- `traaviis/evalone.py:145` builds a `FindingV1` and calls `identity.finding_id`
- `traaviis/evalone.py:291` builds a receipt and calls `identity.episode_id`
- `traaviis/pack.py:387` builds a manifest and calls `identity.environment_id`
- `traaviis/episode_bundle.py:601-617` re-derives six rungs from documents it has
  just read off disk
- …and 30 more of exactly that shape.

**Zero** call sites derive a canonicalizer from an id string. There is no
dispatch table keyed on a prefix, no `parse_id`, no function that takes a string
and returns a serializer.

The two verification entry points confirm it:

- `trvs verify-episode` takes `episode_dir` — a **directory path**
  (`traaviis/cli.py:1673`, `cmd_verify_episode` at `traaviis/cli.py:726`). It
  never receives an id. `verify_episode_bundle` (`traaviis/episode_bundle.py:477`)
  opens `episode-bundle.json`, reads the receipt, and recomputes every id from
  the saved bytes.
- The MCP resource read (`traaviis/mcp.py:736`) does receive a bare id inside a
  URI, and what it does with it is: (a) `startswith("episode-")`, (b) reject
  separators and `..`, (c) join it as a *path component*, (d) load the receipt,
  (e) `receipt.get("episode_id") != episode_id` → refuse
  (`traaviis/mcp.py:794-805`). The id is a lookup key and a self-consistency
  check. It is never a source of interpretation.

The ORS HTTP surface treats ids as opaque by construction:
`_TASK_RE = re.compile(r"^/tasks/([^/]+)$")` (`traaviis/ors_server.py:74`).

There is a stronger fact than any of that. **Every id-bearing document already
carries its own version string, and that string is already inside the hash.**
`episode_version` is the first entry of `_EPISODE_IDENTITY_KEYS`
(`traaviis/identity.py:447`); `environment_version` the first of
`_ENVIRONMENT_IDENTITY_KEYS` (`traaviis/identity.py:471`); `trace_version` is
written explicitly into the canonical trace (`traaviis/identity.py:404`); and
`snapshot`, `reward`, `task`, `finding`, `patch` and `bundle` project out only
their own id field, so their `*_version` is hashed too. The repository *also*
already dispatches on those strings, and already refuses rather than guesses:
`traaviis/bundle.py:202`, `traaviis/pack.py:236`, `traaviis/episode_bundle.py:246`
and `:533` all read `unsupported <x>_version %r` and stop.

So the machinery Option B asks for — a scheme declaration that is inside the
hashed document, and a reader that rejects unknown values instead of guessing —
is not new machinery. It is a fourth instance of a pattern the codebase uses in
three places already.

---

## 2. Per-rung divergence: what actually diverges today, and what could

Nine rungs mint ids (`traaviis/identity.py:39-51`, `__all__`). The audit brief listed seven;
`finding-` and `patch-` were omitted, and `finding-` turns out to matter most.

Measurement: every JSON document in the working tree **and inside both published
`dist/*.zip` packets** was walked (70 dict-valued documents), typed by its own
`*_version` field, projected through the real canonicalizer, and compared byte
for byte against the RFC 8785 reference implementation in `test/test_canonical.py`.

| rung | docs walked | diverging today | floats inside identity today | structurally reachable divergence |
| --- | --- | --- | --- | --- |
| `snap-` | 9 | 0 | none | **astral-plane key** — `files` / `file_modes` are keyed by filesystem paths; `paths.safe_relposix` constrains structure, not charset (proved reachable by `test_canonical.py` C17) |
| `finding-` | 3 | 0 | none | **anything** — see below |
| `patch-` | 0 | 0 | none | `diff` is a normalized string; other keys are producer-set. Low. |
| `trace-` | 3 | 0 | none | `_TRACE_EVENT_KEYS` (`identity.py:390`) is a fixed 12-key allowlist of strings, string-lists and `exit_code`. Effectively closed. |
| `rew-` | 9 | 0 | `signals.*.weight` ×5, `caps[*].reward_max` ×3, per doc | **hand-authored floats.** `1.0` is the natural single-signal weight and the natural uncapped `reward_max`; both diverge. `0.2`/`0.25`/`0.3` happen not to. |
| `task-` | 9 | 0 | none (`timeout_seconds: 30` is an int) | `timeout_seconds: 1.5` conforms; `30.0` diverges. `agent_run_policy.environment` is a map keyed by env-var names. |
| `episode-` | 3 | **3** | `reward` (the only float) | already realized |
| `env-` | 0 | 0 | none | entries are `{path, task_id}` / `{path, reward_id}` (`pack.py:302,334`) — ids and paths. Closed against floats. |
| `bundle-` | 0 | 0 | none | members are `{path, sha256, mode}` (`bundle.py:72`) — three strings. Closed against floats. |

The three diverging documents are three copies of **one logical receipt**:
`examples/eval-one/episodes/episode-42d0…/receipt.json` in the working tree, and
the same file inside both `dist/` packets. Its `reward` is `1.0`.

Two things this table says that the brief did not:

**`finding-` is the widest exposure, and it is adversary-controlled.**
`evalone._finding_artifact` (`traaviis/evalone.py:128-146`) takes
`result["finding"]["citations"]` **verbatim** from the agent's `result.json` —
the only validation is `isinstance(citations, list)` — and hashes it into
`finding_id`. Executed:

```
citation {"confidence": 1.0}            -> finding- diverges: True
citation {"\U0001F600": 1, "דּ": 2} -> finding- diverges: True
summary "\ud800" (lone surrogate)       -> UnicodeEncodeError (untyped, not an IdentityError)
```

`finding_id` is carried in the receipt's `outputs`, which is inside
`_EPISODE_IDENTITY_KEYS`. So the untrusted party in this system can already
choose which side of the divergence an `episode-` lands on. That is not a
conformance nicety; it is the one place where the divergence is reachable by
somebody who is not the maintainer.

**`episode-` diverges much less often than "essentially every episode".** The
brief says essentially every `episode-` ever minted would move. Measured against
the real scorer (`traaviis.reward.score`) over the complete pass/fail outcome
space of both shipped rubrics:

```
residency-demo   32 reachable combinations, 10 distinct rewards
                 2 of 32 (6.2%) produce a reward that diverges: {0.0, 1.0}
residency-forge  identical
```

Partial credit — `0.75`, `0.45`, `0.9` — conforms. Only `0.0` and `1.0` diverge.
The brief's claim is wrong as literally stated and right in effect: `0.0` and
`1.0` are the *modal* outcomes (a clean pass and a tampered/failed run), and
every episode this repository has ever shipped or demonstrated scores `1.0`.
Correcting it matters because it changes the shape of a "constrain the domain"
option: rounding rewards to a conforming representation is not available, since
the two values you cannot avoid are precisely the two that diverge.

**The rungs are a dependency graph, so no rung can be versioned alone.**
From the identity allowlists:

- `task-` contains `reward_id` and the subject's `snapshot_id`
- `env-` contains `task_id`s and `reward_id`s → `bundle-` contains `env_id`
- `episode-` contains `task_id`, `reward_id`, `subject.snapshot_id`, `trace_id`,
  `outputs.finding_id`, `outputs.patch_id` (`identity.py:446-452`)

Stamping a scheme declaration into `rew-` moves `rew-`, and therefore `task-`,
and therefore `env-`, `bundle-` **and** `episode-`. Confining the declaration to
`episode-` is the only placement that moves exactly one rung.

---

## 3. Blast radius: every place an id's *shape* is assumed

Measured by grep over the whole repository plus a runtime probe that fed each
consumer a real legacy id and the Option-A form of the same id.

### 3.1 Inventory

| assumption | count | production | where |
| --- | --- | --- | --- |
| `startswith("<prefix>-")` | **21** | 4 | prod: `mcp.py:783`, `bundle.py:124`, `bundle.py:208`, `episode_bundle.py:255`. tests: `test_runner.py:81`, `test_mcp.py:1024,1604`, `test_bundle.py:668,669,759,760,855,860`, `test_kernel.py:525`, `test_pack.py:426`, `test_cli_evalone.py:113`, `test_evalone.py:186,189,190,323`, `test_ors.py:653` |
| regex over id shape | ~~**3**~~ → **1** | 1 | one shared parser, `scaffold.id_tokens` (`scaffold.py:174`), which the other two sites now call. ~~`scaffold.py:88`, `test_eval_split.py:254`, `test_compare.py:632`~~ — superseded, see §3.4: the two test-local regexes were replaced by calls to `id_tokens`, so the shape is assumed in **one** place rather than three. |
| `split("-", 1)` on an id | **2** | 1 (display only) | `cli.py:61` (`_short`), `test_bundle.py:761` |
| fixed-length slicing for correctness | **0** | 0 | `cli.py:61` truncates to 24 chars for display; nothing slices for meaning |
| URI construction / parsing | **4** | 4 | `mcp.py:302,306,310` build by interpolation; `mcp.py:753` partitions on the first `/` only. No shape assumption in any of them. |
| JSON Schema `pattern` fields | **0** | 0 | there are **no** `*.schema.json` files and no `"$schema"` key anywhere in the repository |
| database columns / indexes | **0** | 0 | there is no database |
| id used as a filesystem path component | **2** | 2 | `episode_bundle.py:263` (`dest_root/episode-<id>/`), `mcp.py:790` |
| third-party / sibling consumers | **0** | 0 | see §3.3 |

### 3.2 Literal ids on disk

| corpus | occurrences | unique values |
| --- | --- | --- |
| TRAAVIIS-owned prefixes, working tree + both `dist/*.zip` | **316** | **61** |
| of which forge-owned (`sem-`, `scen-`, out of scope) | 66 | 8 |
| pinned literals in Python source | **7** | `test_scaffold.py:52` (a fixture string, not a real id), `test_kernel.py:1272` (a comment), `test_kernel.py:1298` (`PRE_LINEARIZATION_EPISODE_ID`), `test_canonical.py:386,616,1201,1643` |
| **live, recomputable ids** (declared id equals the id recomputed from the document) | **36 documents** | **8 distinct values** |

That last row is the number that governs a migration and it is smaller than any
of the others: `task-` ×2, `snap-` ×2, `rew-` ×1, `trace-` ×1, `finding-` ×1,
`episode-` ×1. **Eight.** The other 53 unique literals are illustrative — memo
prose, README casts, `index.html` terminal transcripts. They are wrong-if-copied
today for other reasons (they are truncated with `…`), and nothing verifies them.

Exactly **one** of the eight diverges.

### 3.3 The sibling-consumer boundary

`code/` (the `amp` harness) mentions "traaviis" six times and every one is a
**lane name** — `tools/rename_lanes.py:60,84`, `tools/apply_direction.py:131,293`,
`server.py:6` (a hostname), `amp.py:837` (a log string). The single id-shaped
string in `code/` is English prose at `tools/apply_direction.py:80`. `code/`
parses no TRAAVIIS id and would not notice either option.

`legacy/node-harness/` is the closest thing to a second implementation and it is
not one: it is a terminal harness (`src/{harness,router,stack,repl}.mjs`) with
**no** `createHash`, no `JSON.stringify` canonicalization, and no id derivation.
It is quarantined out of the package and the battery.

**There is currently no second implementation of TRAAVIIS identity in any
language.** That is load-bearing for §5.

### 3.4 The runtime probe — re-measured, and inverted

**This subsection has been rewritten. Its previous finding was the single
strongest cost argument against Option A in this document, and it is no longer
true.** The superseded text is kept below the new measurement, because a reader
who saw the old argument needs to be able to find out what happened to it rather
than to notice silently that it is gone.

**Tree state of this re-measurement.** `835caf7` plus the uncommitted tree, with
`traaviis/scaffold.py`, `test/test_eval_split.py` and `test/test_compare.py` all
**modified and held by another agent at the time of measurement** — that agent's
work on `id_tokens` was still in flight. So this is a reading of a moving file,
and it moved: the probe was run **twice**, and the second run is the one recorded
below. Between the two, `scaffold.py` went `a27531f5acd83e32…` →
`856e83a5540877e1…`, `test_eval_split.py` `7911e26815d9b87f…` →
`3d3a9b300f2b63aa…`, `test_compare.py` `254edb22e1c99425…` →
`dc9918c9daffeff7…`. The Option-A rows were **identical across both runs**; one
row that was not is recorded as superseded at the end of this subsection. If the
hashes have moved again, re-run the probe rather than trusting the block below.

Each consumer was fed `episode-42d0…` (a real 64-hex digest) and
`episode-jcs1-42d0…`:

```
scaffold._ID_LITERAL          GONE — the attribute no longer exists
scaffold.id_tokens(...)       legacy -> [('episode', '42d0…', 'id')]
                              optionA-> [('episode', 'jcs1-42d0…', 'malformed')]
test_eval_split.py:254        now routes through scaffold.id_tokens
test_compare.py:632  (C20)    now routes through scaffold.id_tokens
cli._short(h, 24)             legacy -> 'episode-42d0bb07e5f83e9e5751b6b6'
                              optionA-> 'episode-jcs1-42d0bb07e5f83e9e575'
startswith("episode-")        legacy -> True         optionA -> True
bundle_id.split("-",1)[1]     legacy -> '42d0bb07e5f8…'
                              optionA-> 'jcs1-42d0bb0…'
mcp path-safety check         legacy -> safe         optionA -> safe
```

The `startswith` sites — the largest single category, 21 of them — still all
survive Option A unchanged, because a longer id still starts with the same
prefix. That part of the old finding is intact and is genuinely most of the
inventory.

**The three regexes are gone, and with them the cost argument they carried.**
All three call sites now route through `scaffold.id_tokens`, which recognises the
**rung prefix first** and only then asks whether the remainder is a digest. A
token whose prefix is a known rung and whose remainder does not parse is reported
as `"malformed"`, and all three sites assert `kind != "malformed"`. So under
Option A:

- `scaffold.py`'s L1 guard, "a scaffold invents no identity", **fails** on an
  emitted `episode-jcs1-<hex>` — where the old `_ID_LITERAL` regex passed it;
- `test_eval_split.py:254`'s loop **runs**, on a `malformed` token, and fails —
  where the old `findall` returned `[]` and the body never executed;
- `test_compare.py:632` (C20) **fails** the same way — where `minted` used to be
  the empty set and `minted <= set(_LADDER)` was vacuously true.

Option A's effect on these three guards is now the **opposite** of what this
document reported: it trips all three loudly on the first artifact that carries a
tagged id, rather than silencing all three. That is a migration cost — three
laws to update, deliberately, as part of the change — but it is an ordinary
one, and it is the kind of cost the rest of this document prices at an hour. It
is no longer the decisive argument it was written as.

**What this does to the ranking.** It removes a cost from Option A. It does not
add one to B1 or C, and it does not touch the arguments those rest on (§3.3's
"there is no second implementation", the divergence table in §2, or the
unpriced third-reference cost of promoting the JCS serializer out of the test
file). The ranking in §5 is stated on a criterion — *which option makes the
system's own claims more checkable* — that this measurement does not bear on, so
the recommendation stands. But the *margin* between A and the others is smaller
than this document as originally written implies, and anyone re-litigating the
choice should re-derive it rather than quote §3.4's old conclusion.

**A second thing the first run of the probe found — since fixed.** At
`scaffold.py` `a27531f5…`, prefix-first matching fired on ordinary English:

```
"the episode-level view"  -> [('episode', 'level',    'malformed')]
"a task-specific rule"    -> [('task',    'specific', 'malformed')]
"env-var"                 -> [('env',     'var',      'malformed')]
```

Against a JSON blob of ids that is harmless; against prose — a README, a memo, a
`description` field inside a report — it is a false positive that reads as an
invented identity.

~~"it is recorded rather than acted on because `scaffold.py` is not this
document's to change."~~ **Superseded within the hour.** At `856e83a5…` all four
probes return `[]`: the same agent added a bounded prose exemption, pinned by
`test_scaffold.py`'s L1b ("ordinary hyphenated prose is NOT a violation") with
L1e bounding the exemption so it cannot swallow a real id. Re-measured:

```
"the episode-level view"  -> []      "env-var"      -> []
"a task-specific rule"    -> []      "well-formed"  -> []
```

The Option-A rows are unchanged by that fix — `episode-jcs1-<64hex>` still
reports `malformed` — which is the property the exemption had to preserve and
the reason the finding above still stands. Recorded rather than deleted because
it is a measurement this document made, and a reader who has an older copy needs
to see that it expired rather than that it was wrong.

---

~~**Superseded, 2026-08-04 — the original §3.4 finding.** Kept for the record.~~

> ~~The three regexes are the bad news, and they fail in the worst available
> way. None of them raises. All three simply **stop matching**, and all three
> are written as "assert that nothing unexpected was found":~~
>
> - ~~`scaffold.py:88` + `scaffold.py:672` enforce law L1, "a scaffold invents
>   no identity" — an emitted template must contain no `<prefix>-<hex>` literal.
>   Under Option A a scaffold could emit `episode-jcs1-<hex>` and the guard would
>   pass.~~
> - ~~`test_eval_split.py:254` asserts the evaluation index contains no id family
>   outside `{env, task, episode, snap, sem}`. Under Option A the `findall`
>   returns `[]` and the loop body never executes.~~
> - ~~`test_compare.py:632` asserts a comparison mints no rung of its own. Under
>   Option A `minted` is the empty set and `minted <= set(_LADDER)` is vacuously
>   true.~~
>
> ~~Three guards would go green and blind on the same day. A guard that fails
> open is worse than no guard, because the next person reads the passing test as
> evidence. This is the single most important thing the measurement turned up and
> it is not on the brief's cost list.~~

**Why it was superseded, and what survives it.** The finding was correct when
measured and its *general* claim — a guard written as "assert nothing unexpected
was found" fails open, and Option A is one way to trip it — was right enough that
it was written up as recommendation §5, step 1, "rewrite the three fail-open guards …
independently of the decision". That recommendation was then carried out (by
another agent, on `scaffold.id_tokens`), which is what falsified the specific
measurement. The document is stale here because it was **acted on**, which is the
good failure mode: what expired is the cost, not the reasoning.

---

## 4. The options

### Option A — versioned id prefix (`episode-jcs1-<64hex>`)

**What breaks, measured.** Nothing in the 21 `startswith` sites. Nothing in the
4 URI sites. Nothing in the 0 schema patterns and 0 database columns. **Three
guards fail loudly** and must be updated deliberately as part of the change —
`scaffold.py`'s L1 guard and the two laws that now call `scaffold.id_tokens`
(§3.4). `cli._short` renders 5 characters of scheme tag where it used to render
5 characters of digest — cosmetic, but it is the operator's only view of the id
and it now shows less of what distinguishes one episode from another.
`test_bundle.py:761` still passes but now compares a scheme-tagged string to a
bare SHA-256, which is a weaker assertion than the one that was written.

~~"Three regex guards go silently blind (§3.4)."~~ **Superseded and reversed** —
those three sites now route through one prefix-first parser that reports an
unparseable rung-prefixed token as `malformed` and fails on it. See §3.4 for the
re-measurement, the tree state it was taken against, and what it does and does
not change about the ranking.

**What it costs in this repo's own terms.**

- No new rung, *unless* `IdentityAliasV1` is built — which would be a tenth rung,
  the first one whose subject is other ids rather than an artifact, and it would
  need its own identity allowlist, its own laws, and an answer to "what does an
  alias between two ids of the same document mean when the document is
  content-addressed and therefore already the same document?"
- 0 live ids move. This is Option A's real advantage and it is a genuine one.
- 3 guards must be rewritten *before* the grammar lands, or they lapse.
- 2 permanent grammars. Every future consumer, in every language, forever, must
  accept `episode-<64hex>` and `episode-<tag>-<64hex>`. The regexes above are a
  preview of what that costs at each site.
- The prose contract changes in 9 places (`README.md:880`, `ARCHITECTURE.md:137`,
  `traaviis/identity.py:10`, `scaffold.py:23,87,662`, `test_eval_split.py:249`,
  `TRAAVIIS_ENV_AUTHORING_MEMO.md:61`, `TRAAVIIS_SPLIT_EVAL_MEMO.md:53`).

**What it makes possible.** The canonicalization is derivable from the identifier
alone, which is a real property: a log line containing only an id tells you how
to re-derive it. Given §1, nothing in TRAAVIIS needs that property today, but a
future artifact-store or catalog that indexes ids without storing documents
would.

**What it forecloses.** A single stable id grammar, permanently. `episode-`
becomes a family rather than a shape, and every subsequent scheme change adds a
member. It also weakens the argument the repository makes everywhere else — that
an id is a derivation of bytes — by putting a piece of the derivation *outside*
the bytes, in a namespace that nothing verifies. Nothing would stop a producer
from labelling a `pyjson1`-canonicalized document `jcs1`; the tag is an
unverified assertion in the one place the system otherwise admits none.

### Option B — schema-level canonicalization versioning

Two sub-forms, and the difference between them is most of the cost.

**B1 — bump the existing `*_version` string.** `traaviis.episode.v1` →
`traaviis.episode.v2`, with `v2` meaning "canonicalized per RFC 8785". No new
field. The field is already inside the hash (`identity.py:447`), already the
thing three readers dispatch on, and already the thing `test_canonical.py`'s
`ID_KINDS` uses to type a document.

**B2 — add an `identity_scheme` field.** A separate axis, so a schema change and
a canonicalization change can be told apart. Costs one new key inside every
identity projection it is added to.

**What breaks, measured.** The id grammar does not change, so **all 21
`startswith` sites, all 3 regexes, both `split` sites, all 4 URI sites and both
path-component sites keep working unchanged.** Zero of the §3.1 inventory is
touched. Zero of the 53 illustrative literals become wrong-in-a-new-way.

**What it costs in this repo's own terms.**

- `identity.py` gains a dispatch: read the declared scheme, select the
  canonicalizer, **refuse** an unknown one. `canonical_bytes` today applies
  exactly **three** domain checks — non-finite values (`CANONICAL_NON_FINITE`),
  non-string keys (`CANONICAL_KEY_TYPE`) and unencodable strings
  (`CANONICAL_ENCODING`) — and C18,
  `test_c18_three_domain_checks_are_enforced_and_nothing_else_is`
  (`test_canonical.py:741`), pins that by reading `canonical_bytes`'s own source
  and asserting what its body contains and, more importantly, what it does *not*.
  That law is written to go red when the serializer changes, so it must be
  rewritten deliberately as part of this change. That is its job, not a defect.

  ~~"exactly two domain checks (non-finite values, non-string keys)", pinned by
  `test_c18_two_domain_checks_are_enforced_and_nothing_else_is`.~~
  **Superseded: the count is three and the law was renamed with it.** The cited
  name no longer resolves to anything — which is worse than a stale number,
  because a reader checking this claim against the battery finds no such law and
  cannot tell whether the law was deleted or the citation was wrong. The count
  moving is C18 doing its job (it is designed to go red when the domain changes);
  the dangling citation is this document's defect, not the battery's. Nothing
  else in this section depends on the number: the cost being priced is "one law
  must be rewritten deliberately", and that is true at two checks or three.
- A second canonicalizer, roughly 60 lines. It already exists, tested, as
  `_es_number_to_string` + `_jcs_string` + `_utf16_sortkey` + `_jcs_serialize`
  in `test/test_canonical.py:117-215`, differentially validated against V8 over
  76,926 double bit patterns and against the RFC's Appendix B vectors (C22). It
  is deliberately not exported today ("a yardstick, not a second hasher"). Making
  it the hasher means promoting it out of the test file, which loses the
  independence C21-C24 rest on — a *third* reference would be needed for the
  battery to keep measuring rather than asserting. **This is Option B's largest
  unpriced cost and it is not on the brief's list either.**
- Ids that move: **0 existing**, because existing documents keep `v1` and `v1`
  keeps meaning today's canonicalizer. New artifacts get new ids they would have
  got anyway under any conformance decision.
- One pinned constant moves: `PRE_LINEARIZATION_EPISODE_ID`
  (`test_kernel.py:1298`), because that fixture mints a fresh episode at runtime.
  Its 30-line comment is a record of the last two times it moved for
  environmental reasons and of how much work it took to distinguish "a slice
  moved the identity" from "the ambient `PATH` leaked into the hash". Moving it
  deliberately is cheap; the comment must say so, or the next person re-litigates.
- If applied to all nine rungs: every newly minted id moves, including the eight
  rungs that measurably do not diverge (§2). If applied to `episode-` only: one
  rung moves, and `snap-`/`rew-`/`task-`/`finding-` stay exposed to the future
  divergences in the §2 table — with `finding-` being the agent-controlled one.

**What it makes possible.** "SHA-256 over RFC 8785 canonical JSON, for documents
declaring scheme X" becomes a true sentence with an off-the-shelf implementation
in Go, Rust, JavaScript and Java. The scheme declaration is itself inside the
hash, so it cannot be changed without changing the id — which is the property
Option A's prefix tag does not have.

**What it forecloses.** A bare id string stops being enough to re-derive without
the document. Per §1 that costs TRAAVIIS nothing today. It would cost something
to a future consumer that holds ids without documents, which is not a consumer
that exists.

### Option C — do nothing, document the scheme, never conform

This is the status quo, and it deserves better than the brief's framing, because
**it is already about 80% implemented and it was implemented deliberately.**

- `README.md:880-887` states the divergence and explicitly tells a reimplementer
  *not* to reach for a JCS library, naming `"reward": 1.0` vs `1` as the reason.
- `ARCHITECTURE.md:135-162` states the canonical form, names all four diverging
  number bands and the key-order rule, and says "nothing enforces the agreeing
  subset".
- `test/test_canonical.py` is 1,751 lines that measure the divergence against a
  validated reference and pin each case with a reproducing value. Its own
  docstring says these are characterization laws that will go red on conformance
  and "that is the point".

So Option C's implementation cost is not zero-and-then-some. It is: **write down
that the decision was taken and stop calling it open.** Concretely, `ROADMAP.md`
item B still says "determine whether… if not, decide (see §6)" and §6 decision 1
still reads as unresolved.

**What it costs.** Nothing structural. It permanently forecloses a one-sentence
identity specification, which is the thing item B was ranked #1 to buy.

**What actually breaks if a second-language implementation is written in a year.**
Honestly:

- Not much, *if* it is written from `ARCHITECTURE.md` §3a. Both divergences are
  named there with reproducing values. The key-order half ports cleanly: UTF-8
  byte order and code-point order coincide, so any language's byte-wise string
  sort already matches Python's. The number half does not. Python's rule is
  *shortest round-trip digits, rendered with `repr`'s thresholds* — only the
  first half of that is standard, and the thresholds are specified nowhere
  except CPython's own `float_repr`. A porter must reimplement `1e+16` vs
  `1e16`, `1e-07` vs `1e-7`, and the `.0` suffix on integral floats by reading
  CPython. **(Reasoned from the divergence table, not measured — no port was
  attempted.)** That is the real cost of Option C and it is worse than "port
  from ARCHITECTURE.md" suggests.
- The failure mode if they get it wrong is the good one: ids mismatch loudly at
  the first cross-check, rather than silently agreeing on the wrong document.
- The failure mode if they get it *right* is that TRAAVIIS is now the normative
  reference for a format with no written normative spec, which is the position
  `README.md:880` currently occupies and which the repository objects to
  everywhere else.

The honest summary of Option C is: it is not reckless, it is currently the best
documented of the three, and its cost is entirely reputational-and-future rather
than structural.

---

## 5. Recommendation

**Criterion: choose the option that minimises the number of places where a
verifier can be wrong without failing.**

That criterion is chosen because it is the one this repository already applies
everywhere — `episode_bundle.py` re-derives rather than trusts, `mcp.py:794`
refuses to serve a receipt under a name it does not claim, `test_canonical.py`
measures rather than asserts, and `bundle.py:202` refuses an unknown version
rather than guessing. It is not "minimise migration cost"; on that criterion
Option C wins outright and Option A second.

**By that criterion the ranking is B1 > C > B2 > A.**

**Recommend B1, applied to `episode-` only, and not yet.** Concretely:

1. ~~**Now, and independently of the decision:** rewrite the three fail-open
   guards (`scaffold.py:88`, `test_eval_split.py:254`, `test_compare.py:632`) so
   that each one asserts it found *something* before checking what it found.~~
   **DONE**, by another agent, and it is what falsified §3.4 — see the
   supersession note there. All three now route through `scaffold.id_tokens`,
   which recognises the rung prefix first and reports an unparseable remainder as
   `malformed`, so the absence of a match is no longer expressible as silence.

   The argument for doing it stands as written and is worth keeping: all three
   were fail-open **by construction**, before any grammar change was on the
   table — each asserted the *absence* of an unexpected match, so a regex that
   stopped matching for any reason reported success. `test_compare.py:632`'s
   `minted <= set(_LADDER)` was satisfied by the empty set, and
   `test_eval_split.py:254`'s loop body simply never ran. Option A was one way to
   trip that; a refactor or a renamed prefix was another. It was estimated at an
   hour and correct under every option including doing nothing, and that estimate
   is the one thing here that can no longer be checked.

   **One thing it introduced**, recorded because it is a real cost of the fix
   rather than a reason to undo it: prefix-first matching fires on ordinary
   English (`episode-level`, `task-specific`, `env-var` all report `malformed`),
   so pointing `id_tokens` at prose rather than at a JSON blob of ids yields
   false positives. Measured in §3.4.
2. **Now:** close the `finding-` input domain. `evalone._finding_artifact`
   (`evalone.py:128`) hashes agent-supplied JSON verbatim; that is the only place
   an untrusted party can steer an id's canonicalization, and it also produces an
   untyped `UnicodeEncodeError` on a lone surrogate. Constraining citations to
   `{path: str, start_line: int, end_line: int, quote: str}` removes the widest
   divergence surface in the table and is worth doing whatever is decided here.
3. **Then decide.** If conformance is chosen, B1: `traaviis.episode.v2` means
   RFC 8785. No new field, no new grammar, no new rung, one moved pinned
   constant, and the reader that refuses an unknown scheme is the fourth copy of
   a pattern already in the codebase three times.

Option A is ranked last on this criterion specifically because its scheme tag is
the one piece of a content-addressed derivation that would sit outside the hash
and be verified by nothing. Under B1 a document that lies about its scheme
computes a different id and fails; under A a producer can stamp `jcs1` on
anything and the id still "verifies" against a canonicalizer chosen by the label
rather than by the bytes. That is a new place for a verifier to be wrong without
failing, which is exactly what the criterion excludes.

Under a different criterion the answer changes, and it changes cleanly:

- **"Minimise total cost"** → Option C. It is already written; finish the
  sentence in `ROADMAP.md` and close it.
- **"Maximise the chance a stranger reimplements an id correctly"** → B1.
- **"An id must be self-describing"** → Option A, and the three guards must be
  fixed first.

---

## 6. What would change my mind

- **A consumer that holds ids without documents.** If a catalog, artifact store
  or index is planned that stores `episode-…` strings and not receipts, the
  reviewer's "grammar change is unavoidable" reading becomes correct and A wins.
  Right now §3.3 measures zero such consumers.
- **A second implementation being written before the decision.** If someone is
  already porting, the cost of two grammars is paid by them and the cost of an
  unwritten number-rendering rule is paid by them; that reverses the C-vs-B
  ordering sharply toward B.
- **A measured example of `rew-` or `snap-` diverging in the wild.** Today both
  are 0/9. A single hand-authored `"weight": 1.0` in a real environment would
  make episode-only versioning insufficient and force the whole-ladder question.
- **Finding that the JCS reference cannot be promoted without losing the
  battery's independence in a way that cannot be recovered.** I have asserted
  a third reference would be needed; I have not costed one. If it is expensive,
  B's price rises materially.
- **A ruling that the `*_version` string must mean document-schema only.** That
  kills B1 and leaves B2, which is strictly more expensive (a new key inside
  every projection it touches) but still cheaper than A on this criterion.

---

## 7. Travis's calls, not an implementer's

1. **Conform at all, or close the question.** `ROADMAP.md` §6 decision 1 has been
   open since the audit was ranked #1. The measurement is now in: 8 live ids
   exist, 1 diverges, and the divergence is reachable by an untrusted agent
   through `finding-`. That is enough to decide with; it is not enough to decide
   *for* him, because the argument on the other side — that JCS's benefit is
   entirely prospective and there is provably no second implementation today
   (§3.3) — is real and not disposed of by any number here.
2. **Whether the id grammar is part of the public contract.** This is the actual
   substance of A-vs-B and it is a product decision. If `episode-<64hex>` is a
   promise TRAAVIIS has made, A breaks it and B keeps it. Nothing in the code can
   answer whether it was a promise.
3. **Whether `*_version` may carry canonicalization meaning** (B1) or whether
   that requires its own field (B2). A modelling call with a real cost attached.
4. **Whether the two `dist/` packets are frozen artifacts or rebuildable ones.**
   Under B1 they are untouched. Under a conform-and-rewrite-history variant they
   would both need rebuilding through `tools/accept_packet.py`, and their quoted
   hashes in the memos would move. Worth ruling explicitly, because "the packets
   are frozen" is the assumption C28 (`test_canonical.py:1113`) was written
   under, and it has never been stated as policy.
5. **The zero-budget constraint cuts toward B1 and C and against A.** A's real
   cost is not the edit; it is maintaining two grammars in every consumer
   forever, and consumers are the thing there is no budget to build twice.

---

## 8. Corrections to the brief, recorded rather than rounded off

- The brief lists seven rungs. There are **nine**; `finding-` and `patch-` were
  missing, and `finding-` is the widest exposure of all of them.
- "Essentially every `episode-` ever minted would move" is **false as stated** —
  2 of 32 reachable outcomes diverge (6.2%). It is true in effect, because those
  two are `0.0` and `1.0` and every shipped episode scores `1.0`.
- The brief's Option A cost surface lists "regex validators" generically. The
  measurement found that all three of this repository's id regexes were
  **fail-open guards**, so the cost was not "update three regexes" but "three
  safety properties silently stop holding". Different kind of cost.

  ~~"all three of this repository's id regexes **are** fail-open guards … three
  safety properties silently stop holding."~~ **Superseded — this correction to
  the brief has itself expired.** The three sites now share one prefix-first
  parser and fail loudly instead (§3.4), so the cost really is the ordinary
  "update three call sites" that the brief described generically. The brief was
  wrong about the *reason*; it was closer to right about the *size* than this
  document was. Recorded rather than deleted, because a correction that later
  turns out to have been temporary is worth showing as such.
- The brief does not mention that the JCS reference implementation lives inside
  the test battery as a deliberate yardstick, or that promoting it to be the
  hasher costs the battery its independence. That is Option B's largest unpriced
  item.
- The brief treats Option C as "the status quo with a real argument". It is
  better than that: it is already written down in two places with reproducing
  values, and its only unfinished piece is a `ROADMAP.md` sentence that still
  says the question is open.
