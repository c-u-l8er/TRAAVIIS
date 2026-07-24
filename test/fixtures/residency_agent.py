#!/usr/bin/env python3
"""Deterministic stub agent for the real Residency spec-vs-impl task.

Not a model. It is launched inside the frozen, materialized subject and writes a
fixed structured result (``traaviis.agent-result.v1``) plus a candidate patch,
exercising the full five-signal Residency contract (citations / patch /
finding_completeness / tests / identity). Behaviour is switched by
``TRAAVIIS_STUB_MODE`` so one deterministic agent can drive the 1.0 episode and
every negative case:

  ok          cite spec/residency.md exactly + patch src/mod.py 1->2 (ALL pass)
  wrongcite   same valid patch, but a citation quote that does NOT match the span
  testfail    patch src/mod.py to a value the acceptance test rejects (return 999)
  identityfail  patch world/frozen.wrl to a different VALID world (moves its sem id)
  identitybreak patch world/frozen.wrl to invalid WRL (re-lower errors)
  badpatch    a diff whose context does not match (will not apply)

Each patch is built from the bytes actually on disk, so the unified diff always
carries the exact source line and applies under the strict patch admitter.
"""

import json
import os
import sys

MODE = os.environ.get("TRAAVIIS_STUB_MODE", "ok")


def _read(rel):
    with open(rel, "r", encoding="utf-8") as fh:
        return fh.read()


def _one_line_hunk(rel, lineno, old, new):
    """A minimal single-line-replacement unified diff hunk (a/ b/, 1 line, 0 ctx)."""
    return ("--- a/%s\n+++ b/%s\n@@ -%d,1 +%d,1 @@\n-%s\n+%s\n"
            % (rel, rel, lineno, lineno, old, new))


def _line_index(text, needle):
    """1-based index of the first line equal to ``needle`` (no trailing newline)."""
    for i, line in enumerate(text.split("\n"), start=1):
        if line == needle:
            return i
    raise SystemExit("stub: could not locate %r" % needle)


# The finding cites spec/residency.md line 2 verbatim. In wrongcite mode the quote
# is deliberately wrong so the citations verifier fails on an exact-span mismatch.
spec = _read(os.path.join("spec", "residency.md"))
spec_line2 = spec.split("\n")[1]
quote = "this is not what the spec says" if MODE == "wrongcite" else spec_line2

finding = {
    "summary": "src/mod.py return value is governed by the residency spec",
    "citations": [
        {"path": "spec/residency.md", "start_line": 2, "end_line": 2, "quote": quote}
    ],
}

if MODE == "badpatch":
    patch = _one_line_hunk("src/mod.py", 1, "NOT THE REAL LINE", "return 2")
elif MODE == "testfail":
    old = _read(os.path.join("src", "mod.py")).split("\n")[0]
    patch = _one_line_hunk("src/mod.py", 1, old, "return 999")
elif MODE == "identityfail":
    world = _read(os.path.join("world", "frozen.wrl"))
    old = next(l for l in world.split("\n") if l.startswith("[spinner:sp]"))
    patch = _one_line_hunk("world/frozen.wrl",
                           _line_index(world, old), old, old.replace("n=8", "n=4"))
elif MODE == "identitybreak":
    world = _read(os.path.join("world", "frozen.wrl"))
    old = next(l for l in world.split("\n") if l.startswith("[spinner:sp]"))
    patch = _one_line_hunk("world/frozen.wrl",
                           _line_index(world, old), old, "[spinner:sp]@@BROKEN@@")
else:  # ok
    old = _read(os.path.join("src", "mod.py")).split("\n")[0]
    patch = _one_line_hunk("src/mod.py", 1, old, "return 2")

with open("candidate.patch", "w", encoding="utf-8") as fh:
    fh.write(patch)

with open("result.json", "w", encoding="utf-8") as fh:
    json.dump({
        "format": "traaviis.agent-result.v1",
        "finding": finding,
        "patch_path": "candidate.patch",
    }, fh)

sys.exit(0)
