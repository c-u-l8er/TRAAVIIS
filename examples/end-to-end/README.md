# One pass through the whole stack

This directory holds the pinned result of a single run that crosses all four
repositories in the [&] execution substrate:

```
WRLM proposes  ->  WRL seals  ->  TRVM reduces  ->  TRAAVIIS admits
```

Run it:

```bash
python3 tools/end_to_end.py
```

It prints every intermediate identity and then checks each one against
`PINNED.json`. Exit `0` means every id reproduced.

## Why this exists

Each of the four repositories has a green battery. None of them had evidence
that the **composition** works, and a battery that passes in four repositories
separately is precisely the evidence a layering error survives — every seam gets
tested from one side, by the people who own that side, against fixtures that side
wrote.

`STACK_COMPLETION.md` recorded the gap in its own words: *the WRL/TRVM/WRLM/
TRAAVIIS chain has never been exercised end-to-end as a single pass by an outside
consumer.* This is that pass.

## What you should see

```
WRLM proposes  (offline -- no engine is loaded for this stage)
  spec             wrlm.coverage.v1.2 / wrlm.generator.v1 v2
  seed             traaviis-end-to-end-v1
  pool             58 records, 58 admitted / 0 excluded by binding, sha256 1909d6aab2108cce
  goal-            goal-154143f167318354e93424bb6809d63ae6b21065cc3c25536db3677f17f185d8
  task-            task-7e4d9c43fdf79c860745b46c0e015662e0bc427aa1bdb2118be398ed92310fe1
  case-            case-5168765b781af152fd35b2900bd0004a99040ceeaa13565e27eb9ee7cbb37256

Forge seals, TRVM folds
  engine           v0.7.0-alpha.5 (api 1)
  sem-             sem-75c7e5413358ee6188c97505991d2ea5ecc98aad86cf363cf164d5145c2fc3e0
  scen-            scen-2be578f63401d0a424be78f069cc81278efcb3469ae405ec66a2a340c26b8842
  film             7 epochs, ic_ref reducer

TRAAVIIS admits
  env-             env-3956ef977956f9e8d186da1b168beaff7063f8f4ad56d7bb804d7ffa97912293
  bundle-          bundle-223691f87373c1ef8d1c58c5c2fa797f5837acecbc5ddd330935d637038f7f59
  verify-bundle    exit 0  closed
```

## The two findings

**The joins hold.** The world's identity is derived three separate times by three
separate pieces of software, and they agree:

| who | how |
|---|---|
| WRLM | carries a `sem-` proved once, at capture, by something that had a Forge |
| Forge | asked independently, lowers the source text |
| `trvs pack` | re-lowers a third time, from the bytes that landed on disk |

That agreement is the WRL half of the stack's central claim, and this is the
first time it has been executed as a claim rather than asserted as a design.

**The chain stops before an episode, and it stops by name.**

```
  eval             exit 2  [SUBSTRATE_NOT_EVALUABLE]
```

A `trvm.world.v1` package packs and verifies and **cannot be evaluated**: no
`EpisodeKernelV1` implements TRVM episode semantics, because the D5 process model
is ruled and unbuilt. So the honest end of this pass is `bundle-`, not
`episode-`.

That refusal is pinned as an outcome rather than tolerated as a limitation. If
somebody builds the TRVM kernel, `tools/end_to_end.py` **fails**, and the failure
is the notification that the pass can now go further. A demonstration that
quietly stopped early could not tell the difference between *not built yet* and
*broken*.

## Reading a failure

Three exit codes, the same three readings the rest of the repository uses:

| code | reading | means |
|---|---|---|
| 0 | reproduced | every stage ran, every id matched |
| 1 | drift | a stage ran and disagreed — a real finding |
| 2 | unavailable | a stage could not run at all |

`unavailable` is deliberately not `drift`. A missing engine is not evidence that
an id moved, and a run that could not reach a claim must never be reported as a
run that refuted it.

Every id here is a function of inputs pinned beside it — the coverage spec
version, the generator id and version, the sha256 of the proved pool, and the
engine's `bench_version`. A drift report names those too, so "what moved?" is
answerable from the failure output without re-running anything.

## Prerequisites

The pass needs both sibling repositories present. In the `ProjectAmp2` monorepo
layout they are found automatically; otherwise:

```bash
export TRVS_FORGE_DIR=/path/to/TRVM/forge
export TRVS_WRLM_DIR=/path/to/TRVM/wrlm
```

If either is set to something that is not what it claims to be, the pass refuses
with exit 2 rather than searching on. Being handed ids derived from an engine you
did not choose is worse than being told the engine you named is wrong.

## Regenerating the pins

```bash
python3 tools/end_to_end.py --write
```

Only correct when you intend the recorded ids to move, and the reason belongs in
the commit message. `PINNED.json` is the artifact; the script is the law that
checks it.
