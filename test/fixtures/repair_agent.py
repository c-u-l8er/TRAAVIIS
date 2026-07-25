#!/usr/bin/env python3
"""Deterministic stub agent for the `residency-repair` task.

Not a model. It is launched inside the frozen, materialized subject and writes a
fixed structured result (``traaviis.agent-result.v1``) plus a candidate patch.
The subject it lands in has a real defect -- `spec/contract.md` requires the
module to return 2 and `src/mod.py` returns 1 -- so unlike the conformance stub
next to it, "produce an admissible patch" is not enough here: the patch has to
actually fix the bug or the target test stays red.

The first argv element selects the candidate:

  ok        cite the contract, patch src/mod.py 1 -> 2      (the repair)
  nofix     cite the contract, patch 1 -> 3                 (admissible, wrong)
  gutspec   repair the module AND blank out the contract    (passes the target
            test by satisfying it, fails the health control by destroying the
            thing that made the target meaningful)

The mode arrives as an ARGUMENT rather than an environment variable on purpose.
`runner._seal_env` builds the child environment solely from the task's
``agent_run_policy.environment`` and never inherits the host's (§10a), so an
exported variable provably cannot reach this process. Putting the mode in argv
also models the situation correctly: these are three candidate agents answering
one task, so the task bytes -- and therefore ``task-…`` -- must stay identical
across all three.

`gutspec` is the reason the test plan carries a second command. A candidate that
satisfies the target check while wrecking the repository is the classic way an
acceptance test gets gamed, and a task with only a target test would score it
1.0. Every patch is built from the bytes actually on disk, so the unified diff
carries the exact source lines and applies under the strict admitter.
"""

import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
if MODE not in ("ok", "nofix", "gutspec"):
    raise SystemExit("stub: unknown mode %r" % MODE)

SPEC = os.path.join("spec", "contract.md")
MOD = os.path.join("src", "mod.py")


def _read(rel):
    with open(rel, "r", encoding="utf-8") as fh:
        return fh.read()


def _hunk(rel, start, old_lines, new_lines):
    """A unified diff section replacing `old_lines` at `start` with `new_lines`."""
    body = "".join("-%s\n" % l for l in old_lines)
    body += "".join("+%s\n" % l for l in new_lines)
    return ("--- a/%s\n+++ b/%s\n@@ -%d,%d +%d,%d @@\n%s"
            % (rel, rel, start, len(old_lines), start, len(new_lines), body))


def _line_index(text, needle):
    for i, line in enumerate(text.split("\n"), start=1):
        if line == needle:
            return i
    raise SystemExit("stub: could not locate %r" % needle)


spec = _read(SPEC)
spec_lines = spec.split("\n")

# Cite the sentence that actually states the contract, verbatim. The citations
# verifier compares the quote to the source span exactly, so this is a claim
# about the spec's bytes, not a paraphrase of them.
contract_lineno = next(
    i for i, line in enumerate(spec_lines, start=1) if "That integer is 2." in line
)
finding = {
    "summary": "src/mod.py returns 1; spec/contract.md requires 2",
    "citations": [
        {"path": "spec/contract.md",
         "start_line": contract_lineno,
         "end_line": contract_lineno,
         "quote": spec_lines[contract_lineno - 1]},
    ],
}

mod_old = _read(MOD).split("\n")[0]
mod_new = "return 3" if MODE == "nofix" else "return 2"
patch = _hunk(MOD, 1, [mod_old], [mod_new])

if MODE == "gutspec":
    # Replace every spec line with an empty one: the file survives, its content
    # does not, so `spec.strip()` goes falsy and the health control turns red.
    body = spec_lines[:-1] if spec_lines and spec_lines[-1] == "" else spec_lines
    patch += _hunk(SPEC, 1, body, [""] * len(body))

with open("candidate.patch", "w", encoding="utf-8") as fh:
    fh.write(patch)

with open("result.json", "w", encoding="utf-8") as fh:
    json.dump({
        "format": "traaviis.agent-result.v1",
        "finding": finding,
        "patch_path": "candidate.patch",
    }, fh)

sys.exit(0)
