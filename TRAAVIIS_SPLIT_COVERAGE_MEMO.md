# Items 11 and 12 — split-level coverage, and the paired 0.55 demonstration

**Status.** Implemented and measured on this box, 2026-08-06, under the
best-effort development profile the 9F-A ruling authorized. Everything below
carries `strict_comparison_eligible: false`, enforced by the code rather than
promised by this memo.

---

## 1. The design question was arithmetic, not plumbing

`response_coverage` reads one episode. Aggregating a split has **two defensible
answers, and they disagree**:

```
pooled   sum the numerators, sum the denominators, divide
         -> every unit of rubric weight counts once

mean     average the per-episode ratios
         -> every episode counts once
```

One heavy fully-answered task and nine light unanswered ones:

```
pooled weight coverage   50%
per-task mean            10%
pooled signal coverage   10%
```

Either is a correct answer to a different question. A reader shown one of them
and told it is "the coverage" has been misled about the other, so both are
reported and **neither is named `coverage`** — a key by that name would be read
as the answer and make the others decorative. That is the same refusal
`coverage.py` already makes between weight and signal count.

`S1` constructs the divergence rather than hoping for it, and pins the exact
pair. `S11` pins a case where the two *coincide* (77.5% both), so `S1`'s gap
reads as a property of skew rather than as noise.

---

## 2. Three denominator rules, each closing a way to inflate the number

**Unreadable tasks are counted and excluded.** A task with no receipt — the
agent would not launch, admission refused the fixture — stays in `tasks`, is
absent from every ratio, is listed by id, and makes the split ineligible.
Dropping it silently would inflate coverage by discarding exactly the tasks that
went worst; scoring it zero would invent an adjudication that never happened.

**`error` episodes stay in the denominator** (the 9E ruling, applied). An
episode whose verifiers errored has a receipt, a reading and a real denominator;
excluding it would let a split raise its coverage by breaking verifiers, which
is the shape of every erasure route in this repository. `S3` measures it as the
difference between two splits identical but for the error episode — "it is
included" is a claim about a denominator, and a denominator is what a reader
cannot see.

**`None` means no denominator, never zero.** Reporting zero would be the
strongest possible claim from the least possible evidence — the same
inference-from-absence the containment slices spent two rounds removing.

---

## 3. An aggregate is only as strong as its weakest member

`S5` refuses to certify a mixture of a certified and a best-effort episode. That
is the laundering path: two episodes aggregated into one figure that *looks*
publishable and inherits the stronger label from neither. The profiles are
listed so the mixture is visible, the arithmetic is unaffected, and
`strict_comparison_eligible` is false whatever the ratios say.

An **unclassified** profile is refused too, not assumed fine — the same rule
`execfacts.strict_comparison_eligible` applies one level down.

---

## 4. Item 12: one number, two epistemic states

| | verifiers wired | `tests` / `identity` | reward | weight coverage |
| --- | --- | --- | --- | --- |
| **A** | all five | `fail` / `fail` | **0.55** | **100%** |
| **B** | three | `not_applicable` / `not_applicable` | **0.55** | **55%** |

`reward.score` gives a `fail` and a `not_applicable` the identical `0.0` (§6a),
so both receipts post the same number, the same `status: ok`, the same
`validity: valid`. **Nothing in the reward can tell them apart.**

In **A** the missing 0.45 is a *verdict* — two verifiers looked and said no. In
**B** it is a *gap* — nothing was ever asked. That is the entire justification
for `response_coverage` existing, demonstrated rather than asserted.

`S10` asserts the rewards are identical **first**. If they differed, the pair
would be showing that a score *does* distinguish them, which is the opposite
claim, and the law would be proving the reverse of its own name.

`S11` aggregates the pair: pooled 77.5%, `{"identity": 1, "tests": 1}`
unanswered, and a reward mean that cannot move because the two episodes score
the same. **That gap is the deliverable of items 11 and 12 together** — a
split-level number a reward mean cannot produce, over evidence a reward mean
cannot see.

---

## 5. Purity

`split_coverage` is a pure function: no I/O, no clock, no engine, no identity.
`S7` reads that off the parse tree rather than trusting the docstring. It mints
no id and is never stored, for the reason `response_coverage` and
`ComparisonV1` are not: every input is already hash-bound inside the episodes it
came from, so storing the aggregate would move ids to record something those
bytes already determine.

---

## 6. Two mistakes, both caught by a law *skipping* rather than passing

`S9` — the one law that composes with real sealed bytes rather than hand-built
numbers — looked for the episode bundle at a path that does not exist, and then
for a manifest member called `reward_spec` when the manifest calls it `reward`.
Each time it **skipped**, which is how a law quietly stops testing anything.

Both were mine, and both were visible only because a skip is reported as a skip.
The packet gate would have caught them regardless: G6 forbids skips with the
engine present, so the packet would have been REJECTED rather than shipping a
law that tested nothing. That is the backstop doing exactly what it was added
for after the 9D J15 rejection.

---

## 7. Measured

```
test/test_splitcoverage.py     11 passed, 0 skipped, 0 failed
full battery                  805 passed, 0 skipped, 0 failed  (36 files)

packet  traaviis-11-12-pre.zip
        d6cb637a7b79591334fd3b0adebd5c76c79508f74934bc296a436f434a2cab8a
        G5  636 passed, 169 skipped, 0 failed
        G6  805 passed,   0 skipped, 0 failed
        ACCEPTED
```

Against 9F-A: G5 625 → 636, G6 794 → 805.

---

## 8. Still blocked, and not on code

```
13  v2 cutover              certified backend
14  [&]code integration     certified backend
9F items 2-7                a host that can isolate the control plane
```

Two things worth ruling before the cutover, neither urgent:

**The v2 capability vector names `memory_max_bytes` and `pids_max` as sealed
fields.** Those are values a certified backend *sets*, and this host cannot set
them, so they are deliberately **not** stubbed into the vector. A placeholder
`null` would be indistinguishable from a measured absence, which is the
distinction `UNKNOWN` was added to preserve.

**`compare_episodes(strict=…)` still defaults to `False`.** Correct today — the
entire corpus is best-effort and a `True` default would refuse all of it — but a
caller who forgets the flag gets a comparison of best-effort evidence with no
complaint. The flip belongs to the cutover; it is recorded here so it is not
discovered as a surprise then.
