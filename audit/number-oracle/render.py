"""ES2019 (ECMA-262 10th ed.) 7.1.12.1 NumberToString, applied to digits the
Rust oracle supplied. The branch constants 21 and -6 are QUOTED from the
standard, not inferred: steps 6/7/8 of that section.

Input:  the (s, n) triple as Rust's `{:e}` prints it (`<digits>e<n-1>`).
Output: the ECMAScript string.
"""
import struct, sys
from decimal import Decimal

def render(sign_neg, digits, n):
    k = len(digits)
    if k <= n <= 21:                       # step 6
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:                      # step 7
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:                      # step 8
        s = "0." + "0" * (-n) + digits
    else:                                  # steps 9/10
        e = n - 1
        mant = digits if k == 1 else digits[0] + "." + digits[1:]
        s = mant + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return ("-" + s) if sign_neg else s

def from_rust(hexbits, field):
    x = struct.unpack(">d", bytes.fromhex(hexbits))[0]
    mant, _, exp = field.partition("e")
    neg = mant.startswith("-")
    mant = mant.lstrip("-")
    digits = mant.replace(".", "")
    n = int(exp) + 1
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]; 
    if digits == "0":                      # zero: step 2 of 7.1.12.1 returns "0"
        return "0"
    return render(neg, digits, n)

def cpython_digits(hexbits):
    """Third lineage: CPython's David Gay-derived repr, read through Decimal."""
    x = struct.unpack(">d", bytes.fromhex(hexbits))[0]
    if x == 0: return "0"
    neg = x < 0
    sign, ds, exp = Decimal(repr(abs(x))).as_tuple()
    ds = list(ds)
    while len(ds) > 1 and ds[-1] == 0:
        ds.pop(); exp += 1
    return render(neg, "".join(str(d) for d in ds), exp + len(ds))

rust = dict(l.rstrip("\n").split("\t") for l in open("rust.tsv") if l.strip())
v8   = dict(l.rstrip("\n").split("\t") for l in open("v8.tsv") if l.strip())

rows, dis_v8, dis_py = [], [], []
for h in sorted(rust):
    want = from_rust(h, rust[h])
    py   = cpython_digits(h)
    if want != v8[h]: dis_v8.append((h, want, v8[h]))
    if want != py:    dis_py.append((h, want, py))
    rows.append((h, want))
    # the round-trip property, checked on the oracle's own output
    x = struct.unpack(">d", bytes.fromhex(h))[0]
    assert float(want) == x or (x == 0 and float(want) == 0), (h, want, x)

print("vectors:", len(rows))
print("Rust-derived vs V8:      %d disagreements" % len(dis_v8), dis_v8[:5])
print("Rust-derived vs CPython: %d disagreements" % len(dis_py), dis_py[:5])
with open("oracle.tsv", "w") as fh:
    for h, want in rows:
        fh.write("%s\t%s\n" % (h, want))
