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
  unhashable write a well-formed JSON object whose finding cannot be hashed:
             a `\\ud800` escape (parses to a lone surrogate that UTF-8 cannot
             encode) and a bare `NaN` (which the canonicalizer refuses). Both
             are ordinary bytes any agent can write; this is the candidate
             trying to crash its own evaluation instead of being scored by it.
  deepresult write result.json as a legal but pathologically nested JSON
             document (a list nested `_DEEP` deep). `json.loads` cannot decode
             it: it raises `RecursionError`, which is a `RuntimeError` and so
             was never caught by the runner's `(ValueError, UnicodeDecodeError)`
             clause. Same hack as `unhashable`, one layer earlier -- the crash
             lands in `runner.run_agent` before any finding is ever built.
  badname    write a valid finding + patch, and *also* create one file whose
             **name** is not valid UTF-8 (`evil\\xff.txt`). The name is not
             content the agent submits; it is picked up by the evaluator's own
             `os.walk` rescan, comes back surrogate-escaped (`'evil\\udcff.txt'`)
             and lands in the trace's `files_created_digest`. One line, no
             crafted JSON, and strictly easier than either mode above: the
             candidate does not have to write anything malformed at all.
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

if MODE == "unhashable":
    with open("candidate.patch", "w") as fh:
        fh.write("--- a/src/mod.py\n+++ b/src/mod.py\n"
                 "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")
    # Written as raw text, not via json.dump, because the point is the bytes on
    # disk: `\ud800` and `NaN` are what an agent would actually emit, and both
    # survive `json.loads` into Python objects the identity spine cannot seal.
    with open("result.json", "w", encoding="utf-8") as fh:
        fh.write('{"format": "traaviis.agent-result.v1", "finding": '
                 '{"summary": "\\ud800", "citations": '
                 '[{"path": "spec/one.md", "start_line": NaN, '
                 '"end_line": 2, "quote": "beta"}]}, '
                 '"patch_path": "candidate.patch"}')
    sys.exit(0)

#: Nesting depth for `deepresult`. Well past every stack the decoder can have.
#:
#: On CPython <= 3.11 the JSON scanner is bounded by `sys.getrecursionlimit()`
#: (1000 by default), so a few thousand would do. On 3.12+ the bound is the real
#: C stack, measured at call time -- on this host a bare `json.loads` survives
#: 20 000 and dies at 150 000. The bound therefore depends on how deep the
#: *caller* already is, which differs between `python3 test/...` and pytest.
#: 200 000 is chosen so the mode means the same thing in both, rather than
#: passing under one runner and silently not reproducing under the other.
_DEEP = 200_000

if MODE == "deepresult":
    with open("candidate.patch", "w") as fh:
        fh.write("--- a/src/mod.py\n+++ b/src/mod.py\n"
                 "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")
    # Legal RFC 8259 JSON: a nested array, no syntax error anywhere in it. It is
    # written as raw text for the same reason `unhashable` is -- the point is the
    # bytes an agent puts on disk, and `json.dump` of a real 200 000-deep Python
    # object would hit the encoder's own recursion limit in the *agent*, which is
    # the agent crashing, not the evaluator.
    with open("result.json", "w", encoding="utf-8") as fh:
        fh.write("[" * _DEEP)
        fh.write("]" * _DEEP)
    sys.exit(0)

if MODE == "badname":
    # The bytes of a *name*, not of any file the agent submits. `b'evil\xff.txt'`
    # is a legal filename on any POSIX filesystem (a name is bytes, not text),
    # and nothing the agent writes is malformed: it goes on to submit the same
    # valid finding + patch the `ok` mode does. The evaluator's own `os.walk`
    # rescan is what turns it into a Python `str` -- surrogate-escaped, per
    # PEP 383 -- and that `str` is what cannot be hashed.
    #
    # `open` is given the name as `bytes` deliberately: passing a `str` with a
    # lone surrogate would work too, but bytes is what an agent in any language
    # writes, and it makes the point that no Python-specific trick is needed.
    #
    # On a filesystem that enforces UTF-8 names (APFS, most Windows volumes)
    # this raises and the mode degrades to `ok`. The laws that use it assert the
    # surrogate actually appeared, so that platform sees a loud failure rather
    # than a law that quietly stops testing anything.
    with open(b"evil\xff.txt", "wb") as fh:
        fh.write(b"x")
    # deliberately no sys.exit: fall through and submit a *valid* answer.

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
