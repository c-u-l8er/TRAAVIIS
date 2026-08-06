"""Rust supplies k (the shortest digit count). ES2019 "Note 2" supplies which
of the k-digit candidates is `s`: the closest to m, ties to even. Applied here
with exact Decimal arithmetic over the exact binary64 value -- independent of
V8, of CPython's repr, and of the `%.*e` search production uses.
"""
import struct
from decimal import Decimal, Context, ROUND_HALF_EVEN

def render(neg, digits, n):
    k = len(digits)
    if k <= n <= 21:      s = digits + "0" * (n - k)          # ES2019 step 6
    elif 0 < n <= 21:     s = digits[:n] + "." + digits[n:]   # step 7
    elif -6 < n <= 0:     s = "0." + "0" * (-n) + digits      # step 8
    else:
        e = n - 1
        s = (digits if k == 1 else digits[0] + "." + digits[1:]) \
            + "e" + ("+" if e >= 0 else "-") + str(abs(e))    # steps 9/10
    return ("-" + s) if neg else s

def note2(hexbits, k):
    x = struct.unpack(">d", bytes.fromhex(hexbits))[0]
    if x == 0:
        return "0"
    neg, m = x < 0, Decimal(abs(x))           # exact: no rounding here
    r = m.normalize(Context(prec=k, rounding=ROUND_HALF_EVEN))
    sign, ds, exp = r.as_tuple()
    ds = list(ds)
    while len(ds) > 1 and ds[-1] == 0:
        ds.pop(); exp += 1
    digits = "".join(str(d) for d in ds)
    return render(neg, digits, exp + len(digits))

rust = dict(l.rstrip("\n").split("\t") for l in open("rust.tsv") if l.strip())
v8   = dict(l.rstrip("\n").split("\t") for l in open("v8.tsv") if l.strip())

rows, bad = [], []
for h in sorted(rust):
    mant = rust[h].partition("e")[0].lstrip("-").replace(".", "")
    mant = mant.rstrip("0") or "0"
    k = len(mant)
    want = note2(h, k)
    x = struct.unpack(">d", bytes.fromhex(h))[0]
    assert float(want) == x, ("no round trip", h, want)
    if want != v8[h]:
        bad.append((h, want, v8[h]))
    rows.append((h, want))

print("vectors:", len(rows))
print("Rust-k + exact Note 2  vs  V8: %d disagreements" % len(bad), bad[:8])
with open("oracle.tsv", "w") as fh:
    for h, want in rows:
        fh.write("%s\t%s\n" % (h, want))
