# Item A, item K's documentation half, and a release gate that blamed the packet

**Status.** Implemented and measured on this box, 2026-08-07. Three slices, no
identity moved by any of them, and the ruled 1–14 order was already exhausted
before this session started — so everything here comes off `ROADMAP.md`'s ranked
list rather than off a numbered ruling.

**What is asked of GPT-5.6 is at the end (§6), and it is short.** The build order
has genuinely run out of items that do not need either a decision or different
hardware, and this memo's main job is to say which is which so the next session
does not rediscover it.

---

## 0. What landed before any of this

The tree opened with roughly 3,000 lines of finished, gated, memo'd work sitting
uncommitted across four slices — 9D resource totality, 9E containment, 9F-A
containment-capability honesty, and items 11/12 split coverage. They are now
`b9d7871`, landed together with an explicit disclosure that **none is
independently gated**: they all edit the same runner/execfacts/containment seam,
and reconstructing per-slice commits after the fact would have produced
intermediate trees that were never green and never gated.

Measured first, on a full battery run rather than quoted from the memos: **805
passed, 0 skipped, 0 failed, 36 files.** That run was also the first to cover
`test/test_evalone.py`'s last edit, which landed *after* the final packet gate of
the previous session and had therefore never been inside one.

`provenance/` was gitignored rather than committed. A git bundle of this branch
cannot live on this branch: every commit after it lands makes it incomplete, so a
committed copy is wrong by construction. `COMMIT_PROVENANCE.json` — the half a
reader can check offline, since each entry carries `raw_base64` and Git's id is
`sha1(b"commit %d\0" % len(raw) + raw)` — is committed.

---

## 1. Item A: the four-repository claim, executed

`WRLM proposes, WRL seals, TRVM reduces, TRAAVIIS admits` is the sentence four
repositories are organized around, and until `tools/end_to_end.py` ran it had
never been executed. Each of the four has a green battery, which is precisely the
evidence a layering error survives: every seam is tested from one side, by the
people who own that side, against fixtures that side wrote.

`ROADMAP.md` §2.A's cost estimate held exactly — no new module in `traaviis/`,
one script, one example directory, no id moved.

### The joins hold

The world's identity is derived three separate times by three separate pieces of
software, and they agree:

| who | how |
| --- | --- |
| WRLM | carries a `sem-` proved once, at capture, by something that had a Forge |
| Forge | asked independently, lowers the source text |
| `trvs pack` | re-lowers a third time, from the bytes that landed on disk |

All three: `sem-75c7e5413358ee6188c97505991d2ea5ecc98aad86cf363cf164d5145c2fc3e0`.

That agreement is checked **in the run**, not left to the pins file, because it
is a property of the pass rather than of any recorded value: three derivations
that disagree are broken on a machine that has never seen `PINNED.json`.

### The chain does not reach an episode

```
  eval             exit 2  [SUBSTRATE_NOT_EVALUABLE]
```

A `trvm.world.v1` package packs and verifies and **cannot be evaluated**: no
`EpisodeKernelV1` implements TRVM episode semantics, because D5 is ruled and
unbuilt. The honest end of the pass is `bundle-`.

The refusal is **pinned as an outcome**, so building the TRVM kernel *fails* this
script rather than silently extending it. A demonstration that quietly stopped
early could not distinguish "not built yet" from "broken", which is the ambiguity
several previous slices exist to remove from verdicts.

### It falsified an instruction in the roadmap

§4 row 2 said to include item C's coverage line in A's output. Coverage is a
reading over an **episode**, and this pass produces none — so the instruction
assumed A would reach `episode-`, which it cannot for a TRVM world. Recorded as a
retraction in the table rather than quietly omitted.

### Three defects, all mine, all found by testing failure modes

The happy path worked on the first run. Everything below came from asking what
the tool does when it *cannot* do its job.

1. **`engine.try_load()` falls through an invalid `$TRVS_FORGE_DIR` and keeps
   searching.** Right for its own caller — the identity verifier runs under
   `needs_engine=False` and must be able to catch an absent engine — and wrong
   here. `TRVS_FORGE_DIR=/nonexistent` exited **0** and reported a reproduced
   pass, against an engine the operator never named. This is the same hazard
   `resolve_wrlm` refuses three functions above in the same file, so the file
   contradicted itself. The override is now honoured strictly, matching
   `engine._resolve_forge_dir`, before the soft path may run.
2. **`render(None)` raised `TypeError`** on the within-stage drift path — the
   reporter taking the tool down on precisely the path that exists to report a
   finding, which is the shape `evalone` closed as route 5. Handled before the
   renderer, and exercised with a planted mismatch rather than reasoned about.
3. **`--json` emitted the observation and then a success sentence**, so it did
   not parse.

Also recorded: an earlier draft raised `max_nodes` on the pool read, guessing a
pool of artifacts would exceed 100,000 nodes. Measured: 349 KB, inside every
default bound. The raise was removed — a loosened bound nobody needed is a bound
nobody will tighten back.

### What is pinned, and why each field is there

Every id records the inputs it is a function of: coverage spec version, generator
id and version, the pool's sha256, the engine's `bench_version`, and the binding
gate's counts (**58 records, 58 admitted, 0 excluded**). A pool that starts
admitting `asserted` worlds — worlds whose `sem-` was taken on a publisher's word
rather than re-lowered — drifts this pass rather than passing it on weaker
evidence.

Three readings, matching the rest of the repository: `0` reproduced, `1` drift,
`2` unavailable. All four paths were exercised, including a tampered pin (names
the field, both values) and both absent-dependency cases.

**Not claimed:** an outside consumer. `STACK_COMPLETION.md`'s gap read "as a
single pass **by an outside consumer**", and only the first half is closed.

---

## 2. Item K's documentation half: a stated position

`ROADMAP.md` §6.3 named the defect precisely — of *isolate*, *say plainly that
you do not*, and *no stated position*, TRAAVIIS was in the third state, and it is
the only indefensible one. The README now has a `## Threat model` section and a
*Not a sandbox* bullet.

The position: **TRAAVIIS defends the integrity of the evidence and does not
defend the host.** A candidate cannot erase its own failing score, cannot make a
crash indistinguishable from a refusal, cannot tamper with a persisted episode
without replay deriving a different `episode-`. It also cannot be stopped from
using the network, and on this host cannot be resource-limited at all.

Every posture claim is a table row naming the artifact field a caller reads it
from, rather than a sentence asking to be believed. **Writing it that way caught
two errors in my own first draft**, both of which would have shipped as confident
prose:

- `execution_facts.sandbox.filesystem` is literally `"observed"`. I had written
  "rescan, not enforcement" as though that were its value.
- **`strict_comparison_eligible` is not a field on a receipt at all.** It is a
  predicate over the sealed runner profile
  (`execfacts.strict_comparison_eligible`), surfaced on `ComparisonV1` and on
  coverage aggregates. The draft said episodes "carry" it. They do not.

The remaining claims were checked by execution:
`validate_run_policy({"network": "disabled"}, "residency.trusted-local.v1")`
raises `UnsupportedPolicyError` naming the posture actually available; the honest
declaration is accepted; and neither `ors_server.py` nor `mcp_server.py` contains
any authentication path, so "anyone who can route to the port can submit" is
measured rather than assumed.

`--allow-remote` without a reverse proxy is now stated as **not a supported
configuration**. The bearer token stays unbuilt, per this item's own argument: a
token nobody uses is a token that rots.

---

## 3. The release gate said REJECTED when it meant "I could not judge"

Found by using it. A plain `python3 tools/accept_packet.py PACKET.zip` — no
`--forge` — printed `FAIL G6 ... no engine given` and **REJECTED**, exit 1.

`accept_packet.py`'s own module docstring has argued the opposite since the file
was written: *"A run that could not judge must say so instead of returning a
verdict it did not earn."* It was **half implemented**. `main` pre-checked
`args.forge and not os.path.isdir(args.forge)` — the *mistyped* flag — and the
`and args.forge` let the *omitted* flag walk straight past into `gate_g6`, which
raised the generic `GateFailure`.

A third case was worse and nothing caught it: a `--forge` directory that exists
but holds no `forge_api.py` passes `isdir`, so the run started, extracted the
packet, ran the entire battery against an engine that cannot resolve, and failed
every engine-dependent law — REJECTED, on the operator's typo.

Fixed as one predicate and one class:

- `_engine_unusable(forge)` — the three ways to be unusable, in one place. The
  two call sites previously disagreed with each other, which is how the omitted
  case slipped between them.
- `HarnessError`, deliberately **not** a subclass of `GateFailure`, so it cannot
  be swallowed by any of the four `except GateFailure` handlers and reported as a
  packet verdict. The property holds for a caller that never goes through
  `main`'s pre-check, which is checked directly.
- `main` prints **`NO VERDICT — the harness could not judge this packet`**, exits
  2, appends no row to the results table, and **writes no `--report` file** — a
  report is a record of a judgement, and none was made.

Why this is worth a section rather than a line: `c653e15`'s message records *"the
release gate rejected two consecutive packets"*. REJECTED is read here as real
evidence about the tree. A REJECTED that means *you forgot a flag* is exactly
what teaches a reader to discount the ones that mean something.

---

## 4. Measured

```
battery                      805 passed, 0 skipped, 0 failed  (36 files)

packet  traaviis-session.zip
        340c4e8ccd5465867f366f29cfc8f73b6262dfc51690bdc05d12bf57989e1f4b
        G1  manifest well formed              167 entries
        G2  manifest agrees with archive      167 members hashed, all match
        G3  archive metadata canonical        unix modes, fixed ts, sorted
        G4  contents complete and clean       167 members, 36 test files
        G5  battery, engine absent            636 passed, 169 skipped, 0 failed
        G6  battery, engine present           805 passed,   0 skipped, 0 failed
        G7  rebuild extractor independent     reproduced from both extractors
        ACCEPTED
```

That packet is the **code** gate: it was built and accepted before this memo
existed, so it does not contain this memo. That is the same `-pre` convention the
9F-A and 11/12 memos used, and the recursion is not avoidable — a memo cannot
carry the hash of an archive that carries the memo, and a memo that tried would
either be stale or would never terminate.

So the delivered archive's hash and verdict **travel with the delivery rather
than inside it**, and a reader does not have to take either on trust:

```
python3 tools/accept_packet.py <archive> --forge /path/to/TRVM/forge
```

reproduces the verdict from the archive alone, and G7 re-derives the archive's
own hash from two independent extractors while doing it. That is the honest
terminator: the last link in the chain is a command the reader runs, not a number
this file asserts about itself.

The battery total is unchanged from `b9d7871`, which is the correct result rather
than a null one: `run_battery.py` collects only `test/test_*.py`, and all three
slices here add a `tools/` script, an example directory, and documentation.

Commits: `b9d7871` (four slices), `dca81b1` (item A), plus this session's
threat-model and gate work.

---

## 5. What I did not do, and why

- **Item G, the drift audit.** It is longitudinal by construction — pin declared
  versions, *re-resolve over time*, count the cases where the declared version
  was stable and the content was not. It cannot be completed inside a session,
  and the largest catalog is auth-walled.
- **Item J's `agent-` closure.** §2.J's argument is already complete and I agree
  with it — an `agent-` rung would require TRAAVIIS to have an opinion about what
  an agent is, and "not a model router" is a frozen boundary. But *closing a
  deferred identity rung is a ruling*, and this repository treats the ladder as a
  thing that does not move without one. Written up, not enacted.
- **The `eval-` rung.** §2.J's own "not obviously needed before somebody asks for
  it" stands, and nobody has asked.

---

## 6. What needs a ruling, and what needs hardware

The 1–14 order is exhausted. Everything remaining is blocked on one of exactly
three things, and none of them is code:

```
13  v2 / RFC 8785 cutover      a ruling (five calls, IDENTITY_MIGRATION_OPTIONS §7)
14  [&]code -> TRAAVIIS        a certified backend
9F  items 2-7                  a host that can isolate the control plane
D   publish                    a decision: does the repo go public
E   in-toto attestation        follows D (free tiers are public-repo-only)
H   real isolation posture     a product-boundary decision (§6.3), and it moves ids
I   export adapter             a decision: which catalog
J   close `agent-`             a ruling, cost zero
L   Courier                    the largest item; wants a substrate, not a session
```

**The one I would most like ruled, because it gets more expensive monthly:** the
canonicalization cutover. The audit is done and it was not a no-op — the
identity canonicalization does not conform to RFC 8785 and the divergence is
already realized in a live shipped `episode-`. `jcs.py` is written, `N6` proves
the two profiles agree byte-for-byte on everything both admit (so the cutover is
a *narrowing*, not a re-hashing), and `N9` exists specifically to stop the
cutover happening by accident and is written to be **deleted** at item 13.

The blocker is not implementation. It is `IDENTITY_MIGRATION_OPTIONS.md` §7's
five calls, of which the first two are the real ones: **conform at all, or close
the question**, and **is the id grammar part of the public contract**. Nothing in
the code can answer the second — whether `episode-<64hex>` was ever a promise.

A secondary question, raised by this session rather than inherited: the
split-coverage memo lists item 13 as blocked on *a certified backend*, while the
9D memo lists it as *gated on `run_audit.py`, now green*. Those disagree. My
reading is that the certified-backend condition is about **when** to re-mint the
corpus — you only want to move every id once — rather than about whether the
cutover is implementable. Worth confirming, because if that reading is right then
item 13 is unblocked the moment the ruling lands, and if it is wrong then the
ruling can wait for hardware.
