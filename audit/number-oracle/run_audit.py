#!/usr/bin/env python3
"""Rerun the number oracle from a clean extraction. No absolute paths anywhere.

    python3 audit/number-oracle/run_audit.py

Why this file exists
====================
The audit directory shipped in the previous packet made the oracle *inspectable*
and did not make it *rerunnable*, and the difference is the whole value of
shipping it. As reviewed, `verify_vs_node.py` hard-coded a `/tmp/claude-.../
scratchpad` import path and one user-specific Node binary; `gen_oracle.py` wrote
to that same absolute scratchpad; `render.py` and `render2.py` required
`rust.tsv` and `v8.tsv`, neither of which was in the packet; and `t.rs` printed
two values rather than regenerating the 484-vector Rust corpus. Every one of
those files documents what was done. None of them lets anybody else do it.

An oracle nobody can rerun is an assertion. This is the entry point that makes
it a check, and the exact-minimum-length oracle is a **hard cutover gate** for
`JCS_IJSON_CLOSED_NUMBER_PROFILE_V1`: the v2 profile does not go live until this
exits 0 on a machine that is not this one.

What it does, in the ruling's order
===================================
1. **Locates or accepts `node` and `rustc`** — `--node` / `--rustc`, else `$NODE`
   / `$RUSTC`, else `shutil.which`. No path is ever hard-coded and no default
   points at a user's home directory.
2. **Regenerates V8 output** from the included bit patterns, by feeding
   `patterns.txt` to a generated JS program that reconstructs each double from
   its bits and prints `String(x)`. The bits, not a decimal literal: a decimal
   would be re-parsed by V8 and any disagreement about *parsing* would be
   silently folded into a claim about *rendering*.
3. **Regenerates Rust output** by compiling and running a generated program that
   prints `{:e}` for each pattern.
4. **Derives the minimum round-tripping decimal length independently** — over
   exact rational arithmetic (`fractions.Fraction`), not by `%.*e` search (which
   is what production does) and not from `repr` (which is what the battery's
   reference does). Those are the two implementations already in the tree; a
   third that shared either method would be a tautology wearing a third name.
5. **Chooses the closest candidate at that length**, ties to even, again over
   exact rationals. This is ES2019 7.1.12.1 step 5 plus its Note 2, which
   RFC 8785 §3.2.2.3 explicitly requires.
6. **Compares all outputs** — derived vs V8, derived vs the shipped
   `oracle.tsv`, derived-`k` vs Rust-`k`, and the production
   `traaviis.jcs.number_to_string` vs all of them.
7. **Reproduces `oracle.tsv`** byte for byte.
8. **Fails on any drift.**

Exit status
===========
    0   every stage that ran was green
    1   drift — some stage disagreed. This is the failure the gate is for.
    2   incomplete — a required tool was absent, so a stage could not run.

2 is deliberately not 0 and deliberately not 1. A missing `rustc` is not
evidence of drift, and it is not a discharged gate either; collapsing it into
either answer is how "the audit passes" comes to mean "the audit did not run".

The Rust divergence is expected, and it is recorded
===================================================
Rust's shortest-decimal formatter picks *a* shortest round-tripping decimal;
ES2019's Note 2 picks *the* one closest to the value, ties to even. They differ
on 6 of the 484 boundary vectors, one of them RFC 8785's own Appendix B "Round
to even" row `43143ff3c1cb0959` (Rust: `1424953923781206.3`; the RFC:
`1424953923781206.2`). So Rust is a shortest-*length* oracle and is not by itself
an ECMAScript oracle -- which is exactly why step 4 takes only `k` from it and
re-derives `s`. The divergence set is written to `rust_divergence.tsv` on the
first run that has `rustc`, and compared against it on every run after, so the
expected disagreement cannot silently grow.
"""

import argparse
import gzip
import json
import os
import shutil
import subprocess
import struct
import sys
import tempfile
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
#: `audit/number-oracle` -> `audit` -> the package root. Derived, never written
#: down: this file has to work from wherever the packet was extracted.
REPO = os.path.dirname(os.path.dirname(HERE))

PATTERNS = os.path.join(HERE, "patterns.txt")
ORACLE = os.path.join(HERE, "oracle.tsv")
DIVERGENCE = os.path.join(HERE, "rust_divergence.tsv")
BITS_GZ = os.path.join(HERE, "bits.json.gz")
OUT_GZ = os.path.join(HERE, "out.json.gz")

EXIT_OK, EXIT_DRIFT, EXIT_INCOMPLETE = 0, 1, 2


# ------------------------------------------------------- the independent oracle
def _decimal_exponent(value):
    """The `n` with ``10**(n-1) <= value < 10**n``, for an exact positive rational.

    Computed by digit counting and then corrected, rather than by `log10`: a
    float logarithm is wrong at exactly the boundaries this corpus is made of,
    and a boundary oracle that is approximate at boundaries is no oracle.
    """
    n = len(str(value.numerator)) - len(str(value.denominator)) + 1
    while Fraction(10) ** n <= value:
        n += 1
    while Fraction(10) ** (n - 1) > value:
        n -= 1
    return n


def _round_half_even(q):
    """Round an exact rational to an integer, ties to even. ES2019 Note 2's rule."""
    floor = q.numerator // q.denominator
    remainder = q - floor
    if remainder > Fraction(1, 2):
        return floor + 1
    if remainder < Fraction(1, 2):
        return floor
    return floor if floor % 2 == 0 else floor + 1


def _round_trips(digits, n, x):
    """Does ``0.<digits> * 10**n`` parse back to exactly `x`?

    `float()` is the parser, not a renderer, and CPython's is correctly rounded —
    which is the *definition* of round-tripping rather than a second opinion
    about it. Compared on the bit pattern so that `0.0` and `-0.0` can never be
    mistaken for each other.
    """
    parsed = float("%se%d" % (digits, n - len(digits)))
    return struct.pack(">d", parsed) == struct.pack(">d", x)


def shortest_digits_exact(x):
    """`(digits, n, k)` for a positive finite double, by exact rational search.

    ES2019 7.1.12.1 step 5 asks for integers `s`, `k`, `n` with `k` minimal,
    `10**(k-1) <= s < 10**k`, and the Number value of `s * 10**(n-k)` equal to
    `x`; Note 2 resolves the remaining freedom by taking the candidate closest to
    `x`, ties to even.

    Both halves are done here in exact arithmetic over `Fraction(x)`, which is
    the *exact* value of the binary64 — not a decimal approximation of it. So
    "closest" is a fact about the real number the double denotes, and the
    tie-break is decided on an exact equality rather than on whether two floats
    happened to compare equal.

    Independent of the three renderings this audit compares it against: it does
    not call `repr`, does not use `%.*e`, and never asks V8 or Rust anything.
    """
    exact = Fraction(x)
    n = _decimal_exponent(exact)
    for k in range(1, 18):
        scaled = exact / (Fraction(10) ** (n - k))
        s = _round_half_even(scaled)
        n_k = n
        if s >= 10 ** k:            # rounding carried past the digit window
            s //= 10
            n_k += 1
        digits = str(s)
        if _round_trips(digits, n_k, x):
            stripped = digits.rstrip("0") or "0"
            if stripped != digits:
                # `k` is minimal, so `s` cannot end in a zero: a trailing zero
                # would mean `k-1` digits already round-tripped. Asserted rather
                # than quietly stripped, because if it ever fires the search is
                # wrong and silently fixing the symptom would hide that.
                raise AssertionError(
                    "minimal-length digits ended in a zero: %s" % digits)
            return digits, n_k, k
    raise AssertionError("no decimal of 17 significant digits round-trips %r" % x)


def render_es2019(negative, digits, n):
    """ECMA-262 (ES2019) 7.1.12.1 steps 6-10. The constants are the standard's."""
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)                      # step 6
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]               # step 7
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits                  # step 8
    else:
        exponent = n - 1                                  # steps 9 and 10
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        out = "%se%s%d" % (mantissa, "+" if exponent >= 0 else "-", abs(exponent))
    return ("-" + out) if negative else out


def number_to_string_exact(x):
    """The whole independent oracle for one double."""
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("the oracle covers finite binary64 only")
    if x == 0:
        return "0"                                        # covers -0.0
    negative = x < 0
    digits, n, _k = shortest_digits_exact(abs(x))
    return render_es2019(negative, digits, n)


# ------------------------------------------------------------------- tool wiring
def _resolve(name, override, env_var):
    """A tool path from the flag, the environment, or `PATH`. Never a literal.

    The reviewed harness hard-coded one user's Node build. Nothing here may name
    a home directory, a version, or a machine: an audit that only runs where it
    was written is the thing this file replaces.
    """
    if override:
        return override if os.path.isabs(override) else shutil.which(override)
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env if os.path.isabs(from_env) else shutil.which(from_env)
    return shutil.which(name)


def _read_patterns():
    with open(PATTERNS, encoding="ascii") as fh:
        return [line.strip() for line in fh if line.strip()]


def _double(hexbits):
    return struct.unpack(">d", bytes.fromhex(hexbits))[0]


_NODE_PROGRAM = r"""
const fs = require('fs');
const lines = fs.readFileSync(process.argv[2], 'utf8')
  .split('\n').map(s => s.trim()).filter(s => s.length);
const buf = new ArrayBuffer(8);
const u = new BigUint64Array(buf), f = new Float64Array(buf);
const out = [];
for (const h of lines) { u[0] = BigInt('0x' + h); out.push(h + '\t' + String(f[0])); }
fs.writeFileSync(process.argv[3], out.join('\n') + '\n');
"""

_RUST_PROGRAM = r"""
use std::io::Read;
fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).unwrap();
    for line in input.lines() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let bits = u64::from_str_radix(line, 16).unwrap();
        println!("{}\t{:e}", line, f64::from_bits(bits));
    }
}
"""


def run_v8(node, patterns, work):
    """`{hex: String(x)}` straight out of V8, regenerated from the bit patterns."""
    program = os.path.join(work, "v8.js")
    infile = os.path.join(work, "patterns.txt")
    outfile = os.path.join(work, "v8.tsv")
    with open(program, "w", encoding="ascii") as fh:
        fh.write(_NODE_PROGRAM)
    with open(infile, "w", encoding="ascii") as fh:
        fh.write("\n".join(patterns) + "\n")
    subprocess.run([node, program, infile, outfile], check=True)
    return _read_tsv(outfile)


def run_rust(rustc, patterns, work):
    """`{hex: "<digits>e<n-1>"}` out of Rust's `{:e}` — an independent formatter.

    Rust 1.x `core::fmt` float formatting is `flt2dec`: Grisu3 with an exact
    Dragon4 fallback. Stated at its real strength, as the module docstring in
    `traaviis/jcs.py` does — independent *implementation*, not independent
    *algorithm*, since Grisu3 underlies V8's fast-dtoa too. What it rules out is
    one implementation's coding error.
    """
    source = os.path.join(work, "oracle.rs")
    binary = os.path.join(work, "oracle_rs")
    with open(source, "w", encoding="ascii") as fh:
        fh.write(_RUST_PROGRAM)
    subprocess.run([rustc, "-O", "-o", binary, source], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    proc = subprocess.run([binary], input="\n".join(patterns) + "\n",
                          text=True, stdout=subprocess.PIPE, check=True)
    return dict(line.split("\t", 1) for line in proc.stdout.splitlines() if line)


def _read_tsv(path):
    with open(path, encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("\t", 1) for line in fh if line.strip())


def _rust_k(field):
    """The digit count Rust's `{:e}` implies, i.e. its shortest-decimal length."""
    mantissa = field.partition("e")[0].lstrip("-").replace(".", "")
    return len(mantissa.rstrip("0") or "0")


# ------------------------------------------------------------------ the stages
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rerun the TRAAVIIS number oracle from a clean extraction.")
    parser.add_argument("--node", help="path to a node binary (else $NODE, else PATH)")
    parser.add_argument("--rustc", help="path to rustc (else $RUSTC, else PATH)")
    parser.add_argument("--write-divergence", action="store_true",
                        help="(re)record the expected Rust divergence set")
    args = parser.parse_args(argv)

    patterns = _read_patterns()
    print("boundary vectors: %d" % len(patterns))

    drift = []
    incomplete = []

    # --- stage 4/5: the independent oracle -----------------------------------
    derived = {}
    derived_k = {}
    for hexbits in patterns:
        x = _double(hexbits)
        derived[hexbits] = number_to_string_exact(x)
        if x == 0:
            derived_k[hexbits] = 1
        else:
            derived_k[hexbits] = shortest_digits_exact(abs(x))[2]
        # Self-check: the oracle's own output must parse back to the value it
        # was derived from. A renderer that does not round-trip its own input is
        # not worth comparing anything against.
        if float(derived[hexbits]) != x and not (x == 0 and float(derived[hexbits]) == 0):
            drift.append("oracle does not round-trip %s" % hexbits)
    print("independent oracle: derived %d values by exact rational arithmetic"
          % len(derived))

    # --- stage 7: reproduce oracle.tsv ---------------------------------------
    shipped = _read_tsv(ORACLE)
    if set(shipped) != set(derived):
        drift.append("oracle.tsv covers a different vector set")
    else:
        mismatched = [h for h in patterns if shipped[h] != derived[h]]
        if mismatched:
            drift.append("oracle.tsv disagrees on %d vectors: %s"
                         % (len(mismatched),
                            ", ".join("%s (%s != %s)" % (h, shipped[h], derived[h])
                                      for h in mismatched[:5])))
        else:
            print("oracle.tsv:  reproduced, %d/%d" % (len(shipped), len(patterns)))

    # --- stage 6: the production implementation ------------------------------
    sys.path.insert(0, REPO)
    try:
        from traaviis import jcs
    except ImportError as exc:
        incomplete.append("traaviis.jcs is not importable from %s: %s" % (REPO, exc))
        jcs = None
    if jcs is not None:
        bad = [h for h in patterns if jcs.number_to_string(_double(h)) != derived[h]]
        if bad:
            drift.append("traaviis.jcs disagrees on %d vectors: %s"
                         % (len(bad), ", ".join(bad[:5])))
        else:
            print("traaviis.jcs: agrees on %d/%d boundary vectors"
                  % (len(patterns), len(patterns)))

    work = tempfile.mkdtemp(prefix="traaviis-oracle-")
    try:
        # --- stage 2: V8 ------------------------------------------------------
        node = _resolve("node", args.node, "NODE")
        if node is None:
            incomplete.append("node not found (pass --node or set $NODE)")
        else:
            v8 = run_v8(node, patterns, work)
            bad = [h for h in patterns if v8.get(h) != derived[h]]
            if bad:
                drift.append("V8 disagrees on %d vectors: %s"
                             % (len(bad),
                                ", ".join("%s (%s != %s)" % (h, v8.get(h), derived[h])
                                          for h in bad[:5])))
            else:
                print("V8:          agrees on %d/%d boundary vectors"
                      % (len(patterns), len(patterns)))

            # The 76,926-vector differential corpus. `out.json.gz` is the
            # recorded V8 answer; regenerating it from `bits.json.gz` proves the
            # recording, and comparing production against it proves production.
            if jcs is not None:
                bits = [int(b) for b in json.load(gzip.open(BITS_GZ))]
                recorded = json.load(gzip.open(OUT_GZ))
                if len(bits) != len(recorded):
                    drift.append("bits.json.gz and out.json.gz differ in length")
                else:
                    hexes = ["%016x" % b for b in bits]
                    fresh = run_v8(node, hexes, work)
                    stale = [i for i, h in enumerate(hexes)
                             if fresh.get(h) != recorded[i]]
                    if stale:
                        drift.append(
                            "this node disagrees with the recorded V8 output on "
                            "%d of %d corpus vectors" % (len(stale), len(bits)))
                    else:
                        print("V8 corpus:   regenerated %d vectors, identical to "
                              "the recording" % len(bits))
                    wrong = [i for i, h in enumerate(hexes)
                             if jcs.number_to_string(_double(h)) != recorded[i]]
                    if wrong:
                        drift.append("traaviis.jcs disagrees with V8 on %d of %d "
                                     "corpus vectors" % (len(wrong), len(bits)))
                    else:
                        print("jcs corpus:  agrees with V8 on %d/%d"
                              % (len(bits), len(bits)))

        # --- stage 3: Rust ----------------------------------------------------
        rustc = _resolve("rustc", args.rustc, "RUSTC")
        if rustc is None:
            incomplete.append("rustc not found (pass --rustc or set $RUSTC)")
        else:
            rust = run_rust(rustc, patterns, work)
            k_bad = [h for h in patterns if _rust_k(rust[h]) != derived_k[h]]
            if k_bad:
                drift.append(
                    "Rust and the exact oracle disagree on the shortest LENGTH "
                    "for %d vectors: %s" % (len(k_bad), ", ".join(k_bad[:5])))
            else:
                print("Rust k:      agrees on %d/%d shortest lengths"
                      % (len(patterns), len(patterns)))

            # The digit-level divergence is expected and bounded. Rust picks *a*
            # shortest decimal; ES2019 Note 2 picks *the* closest, ties to even.
            diverged = {}
            for h in patterns:
                x = _double(h)
                rendered = render_es2019(
                    struct.pack(">d", x)[0] & 0x80 != 0,
                    (rust[h].partition("e")[0].lstrip("-").replace(".", "")
                     .rstrip("0") or "0"),
                    int(rust[h].partition("e")[2]) + 1) if x != 0 else "0"
                if rendered != derived[h]:
                    diverged[h] = (rendered, derived[h])

            if args.write_divergence or not os.path.isfile(DIVERGENCE):
                with open(DIVERGENCE, "w", encoding="utf-8") as fh:
                    fh.write("# Rust `{:e}` digits vs the ES2019 Note 2 oracle.\n"
                             "# Expected and bounded: Rust picks *a* shortest "
                             "round-tripping decimal,\n"
                             "# ES2019 picks *the* closest, ties to even.\n")
                    for h in sorted(diverged):
                        fh.write("%s\t%s\t%s\n" % (h, diverged[h][0], diverged[h][1]))
                print("Rust digits: recorded %d expected divergence(s) -> %s"
                      % (len(diverged), os.path.basename(DIVERGENCE)))
            else:
                expected = {}
                with open(DIVERGENCE, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("#") or not line.strip():
                            continue
                        h, was, want = line.rstrip("\n").split("\t")
                        expected[h] = (was, want)
                if expected != diverged:
                    drift.append(
                        "the Rust divergence set moved: expected %d entries, "
                        "measured %d (new: %s)"
                        % (len(expected), len(diverged),
                           ", ".join(sorted(set(diverged) - set(expected))[:5])))
                else:
                    print("Rust digits: %d expected divergence(s), unchanged"
                          % len(diverged))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("")
    for line in incomplete:
        print("INCOMPLETE  %s" % line)
    for line in drift:
        print("DRIFT       %s" % line)
    if drift:
        print("\nFAIL: the oracle drifted.")
        return EXIT_DRIFT
    if incomplete:
        print("\nINCOMPLETE: every stage that ran was green, but not every stage "
              "ran. The v2 cutover gate is not discharged by this run.")
        return EXIT_INCOMPLETE
    print("OK: every stage green.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
