"""The Forge lowering worker — one WRL lower, in a process that may be killed.

``python3 -m traaviis.forge_worker`` reads one JSON request from stdin, lowers
the source through the same public ``forge_api.lower_source`` boundary the
in-process adapter used, writes one JSON response to stdout, and exits. It holds
no state, is never reused, and is not a server.

**Why this process exists.** ``evalone.resolve_signal`` contains a verifier that
*stops* — it classifies a raise and a malformed return as ``error`` and keeps the
episode. It cannot contain a verifier that never stops. An infinite loop, a
native deadlock, an ``os._exit``, a segfault or a runaway allocation reaches no
``except`` clause; the evaluating process simply does not come back, nothing is
persisted, and a failing grade is erased exactly as thoroughly as an uncaught
exception erased it. The ``tests`` verifier has never had this problem, because
it has always run its commands as subprocesses under a timeout. The ``identity``
verifier did: it called ``adapter.lower_source(patched[rel])`` in-process on a
WRL source the *candidate* wrote. That asymmetry is what this module removes.

**The request/response contract** (``PROTOCOL_VERSION``), one JSON object each
way, stdin to stdout::

    request   {"source": "<WRL text>"}

    response  {"status": "ok", "ok": true,  "semantic_artifact_id": "sem-…"}
              {"status": "ok", "ok": false, "error": "<compile diagnostic>"}
              {"status": "unavailable", "error": "<why the engine could not answer>"}

``status: "ok"`` means the engine gave a verdict, and ``ok`` says whether that
verdict was a successful lowering. Ordinary invalid WRL is ``status: "ok"`` with
``ok: false`` — a *data* outcome, exactly as ``LowerResultV1`` frames it — while
an engine that cannot be located, imported, or called at all is
``status: "unavailable"``. The parent maps the second to ``ForgeUnavailable``,
preserving the meaning that ``forge_adapter`` already had.

**Three failures have no response at all**, and the parent must treat all three
alike: a non-zero exit, output that is not one JSON object, and no output
because the process was killed on its timeout. Each says the engine did not
answer, which is ``ForgeUnavailable`` (or its ``ForgeTimeout`` refinement) —
never a ``pass`` and never a ``fail``. A worker that dies is never evidence
against the candidate.

**stdout is claimed.** The response channel is a duplicate of fd 1 taken before
the engine is imported, and ``sys.stdout`` is then pointed at stderr for the rest
of the run. An engine that prints — a warning, a progress line, a stray
``print`` in a lowering pass — would otherwise land in the middle of the response
document and turn a good answer into an unparseable one. The engine keeps its
voice; it just does not share the wire.
"""

import json
import os
import sys

__all__ = ["PROTOCOL_VERSION", "STATUS_OK", "STATUS_UNAVAILABLE", "lower", "main"]

#: The request/response contract above. Bumping it is a coordinated change of
#: both sides; it is deliberately **not** part of any identity, because the wire
#: format between a process and its own worker says nothing about the world being
#: lowered. What *does* enter ``verifier_versions.identity`` is the engine
#: boundary version computed by ``forge_adapter.real_adapter`` — unchanged by the
#: existence of this module, which is what keeps every sealed ``episode-…`` where
#: it is.
PROTOCOL_VERSION = "traaviis.forge-worker.v1"

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"


def lower(source):
    """Lower one source; return the response object described in the module doc.

    Total by construction: every failure mode becomes a response rather than a
    traceback, because a traceback on stderr and a non-zero exit is a strictly
    worse report than a structured ``unavailable``. (The parent handles that case
    too — it has to, for a segfault — but it can say less about it.)
    """
    from . import engine

    forge_api = engine.try_load()
    if forge_api is None:
        return {"status": STATUS_UNAVAILABLE,
                "error": "could not locate a compatible Forge engine "
                         "(set TRVS_FORGE_DIR)"}
    fn = getattr(forge_api, "lower_source", None)
    if not callable(fn):
        return {"status": STATUS_UNAVAILABLE,
                "error": "forge_api has no callable lower_source"}
    try:
        payload = fn(source)
    except Exception as exc:  # noqa: BLE001 -- an engine that raises is unavailable
        return {"status": STATUS_UNAVAILABLE,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        if not payload.get("ok"):
            return {"status": STATUS_OK, "ok": False,
                    "error": payload.get("error")}
        return {"status": STATUS_OK, "ok": True,
                "semantic_artifact_id": payload["semantic_artifact_id"]}
    except Exception as exc:  # noqa: BLE001 -- a LowerResultV1 that is not one
        # The engine answered with something the frozen contract does not
        # describe. That is the engine failing to answer, not the candidate
        # failing to compile, so it is `unavailable` and never `ok: false`.
        return {"status": STATUS_UNAVAILABLE,
                "error": "malformed LowerResultV1: %s: %s"
                         % (type(exc).__name__, exc)}


def main(argv=None):
    """Read one request, write one response. Always exits 0 when it responded."""
    # Claim the response channel before anything can import the engine and print
    # on it. See the module docstring.
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr

    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
        source = request["source"]
        if not isinstance(source, str):
            raise TypeError("source must be a string")
    except Exception as exc:  # noqa: BLE001 -- a malformed request is our fault
        response = {"status": STATUS_UNAVAILABLE,
                    "error": "malformed worker request: %s: %s"
                             % (type(exc).__name__, exc)}
    else:
        response = lower(source)

    channel.write(json.dumps(response, sort_keys=True))
    channel.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
