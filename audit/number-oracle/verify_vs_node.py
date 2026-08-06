"""Differential-test the Python es_number_to_string against node (V8) at scale."""
import json
import random
import struct
import subprocess
import sys

sys.path.insert(0, "/tmp/claude-1000/-home-travis-ProjectAmp2/"
                   "aaa796b3-30cd-42f4-9fa0-2d43710658b7/scratchpad")
from jcsref import es_number_to_string

NODE = "/home/travis/.nvm/versions/node/v25.2.1/bin/node"

random.seed(20260804)
vals = []
# uniform random bit patterns (finite only)
while len(vals) < 60000:
    b = random.getrandbits(64)
    f = struct.unpack("<d", struct.pack("<Q", b))[0]
    if f == f and abs(f) != float("inf"):
        vals.append(f)
# structured corpus: powers of ten, small decimals, integers
for e in range(-330, 310):
    try:
        vals.append(float("1e%d" % e))
        vals.append(float("-1e%d" % e))
        vals.append(float("9.999e%d" % e))
    except (OverflowError, ValueError):
        pass
for i in range(0, 3000):
    vals.append(float(i))
    vals.append(i / 7.0)
    vals.append(i * 1e15)
    vals.append(i * 1e20)
    vals.append(i * 1e21)
vals += [0.0, -0.0, 5e-324, -5e-324, 1.7976931348623157e308,
         2.0 ** 53, 2.0 ** 53 + 2, 1e21, 1e-7, 1e-6]
vals = [v for v in vals if v == v and abs(v) != float("inf")]

# Send the exact bit patterns so no decimal parsing can perturb the comparison.
bits = [struct.unpack("<Q", struct.pack("<d", v))[0] for v in vals]
js = """
const fs = require('fs');
const bits = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const buf = new ArrayBuffer(8);
const u = new BigUint64Array(buf), f = new Float64Array(buf);
const out = [];
for (const b of bits) { u[0] = BigInt(b); out.push(String(f[0])); }
fs.writeFileSync(process.argv[3], JSON.stringify(out));
"""
base = ("/tmp/claude-1000/-home-travis-ProjectAmp2/"
        "aaa796b3-30cd-42f4-9fa0-2d43710658b7/scratchpad/")
open(base + "num.js", "w").write(js)
open(base + "bits.json", "w").write(json.dumps([str(b) for b in bits]))
subprocess.run([NODE, base + "num.js", base + "bits.json", base + "out.json"],
               check=True)
expected = json.load(open(base + "out.json"))

bad = []
for v, want in zip(vals, expected):
    got = es_number_to_string(v)
    if got != want:
        bad.append((v.hex(), repr(v), got, want))

print("compared %d doubles against node v25 (V8)" % len(vals))
print("mismatches: %d" % len(bad))
for row in bad[:20]:
    print("  ", row)
