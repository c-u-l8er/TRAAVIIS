#!/usr/bin/env python3
"""Deterministic stub agent for TRAAVIIS runner/eval-one tests.

Not a model. Reads the frozen workspace it was launched in and writes a fixed
structured result + a candidate patch, exercising the one-shot agent contract
(traaviis.agent-result.v1). Behavior is switched by TRAAVIIS_STUB_MODE:

  ok         (default) write a valid finding + a patch that applies cleanly
  badpatch   write a finding + a patch that will not apply (context mismatch)
  nooutput   write nothing (missing required outputs -> fail)
  timeout    sleep past any sane timeout
  escape     write outside the workspace-relative tree (policy violation)
  nonzero    write valid outputs but exit with a non-allowed code (3)
  listresult write result.json as a JSON list (malformed: not an object)
"""

import json
import os
import sys
import time

MODE = os.environ.get("TRAAVIIS_STUB_MODE", "ok")

if MODE == "timeout":
    time.sleep(3600)
    sys.exit(0)

if MODE == "nooutput":
    sys.exit(0)

if MODE == "escape":
    with open(os.path.join("..", "escape.txt"), "w") as fh:
        fh.write("outside the sandbox\n")
    sys.exit(0)

if MODE == "listresult":
    with open("candidate.patch", "w") as fh:
        fh.write("--- a/src/mod.py\n+++ b/src/mod.py\n"
                 "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")
    with open("result.json", "w") as fh:
        json.dump([1, 2, 3], fh)  # a JSON list, not an object
    sys.exit(0)

finding = {
    "summary": "spec/one.md line 2 contradicts src/mod.py",
    "citations": [
        {"path": "spec/one.md", "start_line": 2, "end_line": 2, "quote": "beta"}
    ],
}

if MODE == "badpatch":
    patch = (
        "--- a/src/mod.py\n+++ b/src/mod.py\n"
        "@@ -1,1 +1,1 @@\n-NOT THE REAL LINE\n+changed\n"
    )
else:  # ok
    patch = (
        "--- a/src/mod.py\n+++ b/src/mod.py\n"
        "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n"
    )

with open("candidate.patch", "w") as fh:
    fh.write(patch)

with open("result.json", "w") as fh:
    json.dump({
        "format": "traaviis.agent-result.v1",
        "finding": finding,
        "patch_path": "candidate.patch",
    }, fh)

if MODE == "nonzero":
    sys.exit(3)  # valid outputs, but a non-allowed exit code

sys.exit(0)
