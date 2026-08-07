# The number oracle

RFC 8785 §3.2.2.3 mandates ECMAScript `Number::toString` — ECMA-262 (ES2019)
7.1.12.1 including its "Note 2" enhancement. This directory is the evidence that
`traaviis/jcs.py` implements it, and the harness that lets somebody who is not us
re-earn that evidence.

## The one entry point

```
python3 audit/number-oracle/run_audit.py
```

No arguments needed. It locates `node` and `rustc` on `PATH` (or takes `--node`
/ `--rustc`, or `$NODE` / `$RUSTC`), regenerates both foreign outputs from the
included bit patterns, derives the answer independently over exact rational
arithmetic, compares everything against everything, reproduces `oracle.tsv`, and
fails on any drift.

```
exit 0   every stage that ran was green
exit 1   drift — some stage disagreed
exit 2   incomplete — a tool was absent, so a stage could not run
```

`2` is deliberately neither `0` nor `1`. A missing `rustc` is not evidence of
drift and it is not a discharged gate either; collapsing it into either answer is
how "the audit passes" comes to mean "the audit did not run".

**This entry point is the v2 cutover gate.** `JCS_IJSON_CLOSED_NUMBER_PROFILE_V1`
does not go live until this exits 0 on a machine that is not the one that wrote
it.

## Why it exists

The previous packet shipped this directory to make the oracle *inspectable*. As
reviewed, that did not make it *rerunnable*, and the gap was the whole point of
shipping it:

- `verify_vs_node.py` hard-coded a `/tmp/claude-…/scratchpad` import path and one
  user-specific Node binary;
- `gen_oracle.py` wrote its output to that same absolute scratchpad;
- `render.py` and `render2.py` required `rust.tsv` and `v8.tsv`, neither of which
  was in the packet;
- `t.rs` printed two values — it did not regenerate the 484-vector Rust corpus.

An oracle nobody can rerun is an assertion. `run_audit.py` is what makes it a
check.

## What is data and what is history

| file | what it is |
| --- | --- |
| `run_audit.py` | **the entry point.** Relocatable, no absolute paths. |
| `patterns.txt` | the 484 boundary bit patterns, as hex. Input data. |
| `oracle.tsv` | the expected rendering of each. Regenerated and compared. |
| `bits.json.gz` | the 76,926-double differential corpus, as bit patterns. |
| `out.json.gz` | V8's recorded answer for that corpus. Regenerated and compared. |
| `rust_divergence.tsv` | the expected Rust-vs-ES2019 disagreement set (6 rows). |
| `gen_oracle.py`, `verify_vs_node.py`, `render.py`, `render2.py`, `oracle.js`, `t.rs` | **historical.** The scripts as they were actually run in the session that produced the tables. They carry absolute paths and are kept as the record of what was done, not as something to run. `run_audit.py` supersedes all six. |

## The Rust divergence is expected, and it is bounded

Rust 1.x `core::fmt` (`flt2dec`: Grisu3 with an exact Dragon4 fallback) picks *a*
shortest round-tripping decimal. ES2019's Note 2 picks *the* one closest to the
value, ties to even. They differ on 6 of the 484 boundary vectors — three values
and their negatives — one of which is RFC 8785's own Appendix B "Round to even"
row:

```
43143ff3c1cb0959    Rust 1424953923781206.3    RFC/ES2019 1424953923781206.2
```

So Rust is a shortest-**length** oracle and is not by itself an ECMAScript
oracle. `run_audit.py` uses it only for `k` and re-derives `s` exactly. The
divergence set is recorded so it cannot silently grow: a seventh row is drift.

Stated at its real strength, as `traaviis/jcs.py` already does: Rust is an
independent *implementation*, not an independent *algorithm*. Grisu3 is Loitsch's
and underlies V8's fast-dtoa too, so a defect in the published algorithm would be
invisible to both. What the pair rules out is one implementation's coding error.
