"""``ForgeIdentityAdapterV1`` — the declared seam to the Forge/TRVM re-lower.

GPT-5.6 blocker-2 ruling: the ``identity`` verifier must re-lower a WRL source to a
``SemanticArtifactID`` through a **declared adapter**, never by importing a private
Spinner Bench function (``spinner_bench._lower_payload``). Both Spinner Bench and
TRAAVIIS should eventually consume one stable public entrypoint, expected to be
``forge_api.lower_source(source) -> sem-…``.

This module freezes the seam:

  ``ForgeIdentityAdapterV1``  the interface: ``.version`` (enters
                              ``verifier_versions.identity``) + ``lower_source``.
  ``StubForgeAdapter``        a deterministic in-repo double for the batteries —
                              ``sem-<sha256(source)>``, no engine dependency.
  ``real_adapter()``          binds to the public ``forge_api.lower_source``
                              (LowerResultV1) via the soft engine loader, else
                              raises ``ForgeUnavailable`` (→ identity verifier
                              ``error``, never a false ``pass``/``fail``).

The real adapter lowers in a **killable worker process** (``traaviis.forge_worker``)
under ``FORGE_LOWER_TIMEOUT_SECONDS``, not in the evaluating process. The source
being lowered is the *patched* tree's — bytes the candidate wrote — and an
in-process call has no answer to a source that makes the engine loop, deadlock in
native code, or exit outright: no exception is raised, so nothing is classified,
so no episode is written and the grade is erased. A hang is now ``ForgeTimeout``
(a ``ForgeUnavailable``), which the identity verifier seals as ``error`` under the
stable ``ERROR_CODE_FORGE_TIMEOUT``. The ``tests`` verifier has always had this
boundary; ``identity`` was the asymmetry.

The real adapter reads a **LowerResultV1** payload
(``{result_version, ok, semantic_artifact_id, diagnostics, error, engine_version}``,
plus a success's presentation superset): ordinary invalid WRL is ``ok=False`` (→
verifier ``error``), and only an unreachable engine raises ``ForgeUnavailable``. Its
``.version`` embeds the whole lowering boundary — the engine API version, the frozen
LowerResultV1 contract version, and the engine build
(``forge.identity.v1@api-<api>@lower-<lower-result-version>@engine-<engine_version>``)
— so a drift in any layer yields a different ``verifier_versions.identity`` and thus
a different ``episode-…`` id.
"""

import hashlib
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from . import boundedjson as _bjson, execlimits

__all__ = [
    "LowerResult", "ForgeUnavailable", "ForgeTimeout", "ForgeSourceTooLarge",
    "ForgeIdentityAdapterV1", "StubForgeAdapter", "real_adapter",
    "FORGE_LOWER_TIMEOUT_SECONDS", "ERROR_CODE_FORGE_TIMEOUT",
    "ERROR_CODE_FORGE_SOURCE_TOO_LARGE",
]

#: How long one worker gets to lower one source before it is killed. Generous,
#: because it is not a performance budget: the only run it ever ends is one that
#: was never going to finish. A real lower is milliseconds; an engine import is
#: a fraction of a second.
FORGE_LOWER_TIMEOUT_SECONDS = 120.0

#: The stable, host-independent code the identity verifier seals when a lowering
#: was killed on its timeout. A *code*, never the message: the message would carry
#: a duration, and a duration is a fact about the host, not about the world.
ERROR_CODE_FORGE_TIMEOUT = "FORGE_LOWER_TIMEOUT"

#: The stable code sealed when a WRL source is over the IPC bound. A code for the
#: same reason, and a *different* code from the timeout on purpose: a timeout is
#: a fact about how long the engine took and a size refusal is a fact about the
#: candidate's bytes, so a reader that cannot tell them apart cannot tell an
#: engine problem from a submission problem.
ERROR_CODE_FORGE_SOURCE_TOO_LARGE = "FORGE_SOURCE_TOO_LARGE"


class ForgeUnavailable(Exception):
    """The Forge re-lower engine could not be reached / is not published yet."""


class ForgeTimeout(ForgeUnavailable):
    """A lowering ran past ``FORGE_LOWER_TIMEOUT_SECONDS`` and its worker was killed.

    **A subclass, deliberately.** A worker changes what "unavailable" can mean —
    before it, the engine either answered or the process was gone; now it can
    also simply never answer — and every existing ``except ForgeUnavailable``
    handler already does the right thing with that: report the identity verifier
    as ``error``, never a false ``pass`` or ``fail``. Making the timeout a new
    sibling exception would have left each of those handlers silently not
    covering the case that this whole change exists to cover, which is the
    fail-open shape this repository has removed three times. Callers that want to
    *distinguish* a hang from a missing engine catch this first; callers that only
    care that no answer arrived need no edit.
    """


class ForgeSourceTooLarge(Exception):
    """A WRL source exceeded ``execlimits.MAX_IPC_SOURCE_BYTES``.

    **Not a ``ForgeUnavailable``, and the change of base class is the ruling.**
    9D made it one, on the argument that every existing handler would then treat
    it correctly -- and flagged the tension that created: ``error`` means
    ``reward = None``, the episode goes unscored, and this refusal is
    attributable to the *candidate's own bytes*. A candidate that wrote a 2 MiB
    WRL file could unscore its identity signal by doing so.

    The 9E ruling drew the line at **attribution, not severity**::

        deterministic candidate-byte limit
            -> evidence against the candidate
        host/runtime availability or wall-clock limit
            -> no trustworthy candidate verdict

    A source-size profile is a declared byte bound: the same source is over it
    on every host, so refusing it says something about the submission rather
    than about the machine. It is therefore an identity ``fail`` when the source
    is the *patched* one, and an inadmissible configuration when it is the
    *original* -- that fixture was never evaluable. A wall-clock timeout stays on
    the other side of the line and stays ``error``, because the ruling is
    explicit that it must not become a numerical failure until Forge has a
    deterministic fuel or reduction bound.

    Being a plain ``Exception`` is what makes that stick. Left as a
    ``ForgeUnavailable`` subclass it would keep being absorbed by the existing
    ``except ForgeUnavailable`` handlers and keep coming out as ``error``,
    whatever this docstring said.
    """


@dataclass(frozen=True)
class LowerResult:
    """Outcome of lowering one source: a ``sem-…`` id, or an error string."""

    semantic_id: Optional[str]
    ok: bool
    error: Optional[str] = None


class ForgeIdentityAdapterV1(ABC):
    """Re-lower a WRL source string to its ``SemanticArtifactID``."""

    version: str = "forge.identity-adapter.v1"

    @abstractmethod
    def lower_source(self, source: str) -> LowerResult:
        """Lower ``source`` to a ``LowerResult`` (never raises for a compile error;
        a lowering failure is ``ok=False``). Raise ``ForgeUnavailable`` only when
        the engine itself is unreachable."""
        raise NotImplementedError


class StubForgeAdapter(ForgeIdentityAdapterV1):
    """Deterministic test double: ``sem-<sha256(source)>``, no engine needed.

    Not the real lowering — it exists so the identity-verifier law (unchanged
    source ⇒ unchanged id; changed source ⇒ moved id; compile error ⇒ error) is
    provable in the battery without the TRVM engine. A source containing the
    marker ``@@COMPILE_ERROR@@`` lowers to ``ok=False`` to exercise the error path.
    """

    version = "forge.identity-adapter.stub.v1"

    def lower_source(self, source: str) -> LowerResult:
        if "@@COMPILE_ERROR@@" in source:
            return LowerResult(semantic_id=None, ok=False, error="stub compile error")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return LowerResult(semantic_id="sem-" + digest, ok=True)


# --- The lowering worker boundary (route 6) ----------------------------------
#
# **`subprocess`, not `multiprocessing`.** Four reasons, in descending weight:
#
#   1. *A kill has to reach grandchildren.* `multiprocessing.Process.terminate()`
#      and `.kill()` signal the child and nothing else, and `join(timeout)` is not
#      a kill at all — it is a decision to stop waiting, which is precisely the
#      mistake `mcp_server.drain` documents at length (it stopped waiting, the
#      work went on, and only the workers being non-daemon threads kept the
#      process honest). `tools/run_battery.py` learned the other half the hard
#      way: killing only the direct child left grandchildren holding the pipes,
#      so the battery went on waiting for a file it had given up on. Its fix —
#      `start_new_session=True` plus `os.killpg` — is what is reused below, and
#      `multiprocessing` offers no equivalent.
#   2. *Nothing has to be picklable.* The request is one `str` and the response is
#      one small JSON object. `multiprocessing`'s spawn method would need the
#      target importable and the arguments picklable; a `LowerResultV1` payload
#      from a third-party engine is neither of those by contract.
#   3. *`fork` is unsafe where this actually runs.* `mcp_server` handles each
#      request on its own non-daemon thread, and a verifier therefore executes in
#      a multi-threaded process. Forking there is the documented hazard, and
#      `multiprocessing`'s default start method on Linux has been moving away
#      from `fork` for exactly that reason. A fresh interpreter has no such
#      question to answer.
#   4. *Shared state is the thing being escaped.* The hazards this boundary exists
#      for — a segfault, an `os._exit`, a runaway allocation in native code —
#      are hazards to the *interpreter*. `multiprocessing` with `fork` inherits
#      that interpreter's memory. A separate `sys.executable` shares nothing.
#
# The cost is one interpreter start and one engine import per lowered source,
# paid once per identity binding. It is stated rather than hidden: a task binding
# many sources pays it many times, and batching them into one worker invocation
# is the obvious future optimization. It is not taken now because it would change
# `ForgeIdentityAdapterV1.lower_source`, and the seam's stability is the reason
# that interface was frozen in the first place.


#: The worker is launched **out of this package**, whatever this package is
#: currently called, and never out of a hard-coded ``"traaviis"``.
#:
#: This is not defensiveness about renaming. The batteries' non-vacuity
#: instrument (`test_evalone._isolated_traaviis`, `test_canonical.isolated_package`)
#: works by copying `traaviis/` to `traaviis_cutover_N/` under a temp root and
#: importing that; a source-level deletion is proved by watching the *copy* go
#: red. A worker spawned as `-m traaviis.forge_worker` from inside the copy would
#: either fail to import — silently downgrading every identity verdict in the
#: isolated run to `error` — or, worse, import the real package and answer from
#: a build that is not the one under test. Both make the copy prove something
#: other than what it says. The first is what actually happened, and B4 caught
#: it. The general statement is the same one `real_adapter`'s version string
#: makes: the code that answered must be the code the receipt names.
_PACKAGE = __package__ or "traaviis"
_WORKER_MODULE = _PACKAGE + ".forge_worker"
#: The directory the package lives *in*, i.e. what has to be on the worker's path
#: for `-m _WORKER_MODULE` to resolve to this build.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# The kill-the-whole-group primitive that used to live here is now
# `execlimits.kill_process_group`, and `_lower_in_worker` reaches it through
# `execlimits.run_bounded` rather than open-coding a `Popen` + `communicate`
# pair. That is the substance of the 9D ruling and not a tidy-up: this module
# had the *only* correct process-tree kill in the package while the agent runner
# and the tests verifier each had a bare `subprocess.run(timeout=…)`, so the one
# execution path that had been thought about was also the one that needed it
# least. There is now one primitive and three callers.


def _worker_env(forge_dir: Optional[str]) -> dict:
    """The environment the worker resolves its engine in.

    `TRVS_FORGE_DIR` is set from the engine the *parent* already selected, so the
    worker re-lowers through exactly the checkout whose version was sealed into
    `verifier_versions.identity`. Letting the worker search independently is the
    one way this boundary could silently answer from a different engine than the
    receipt claims, and `real_adapter`'s whole reason for taking an explicit
    `forge_api` was to stop that happening across call sites.

    Note what this does NOT protect against, because `engine.try_load` is not
    `engine.load`: an override that is not a usable engine directory is treated
    as a candidate that did not match and the search continues, so a *wrong*
    `forge_dir` would silently bind whatever the worker finds instead. That is
    tolerable here only because `forge_dir` is not user input — it is
    `os.path.dirname(forge_api.__file__)` of a module the parent already
    imported, so it is a real engine directory by construction. A caller passing
    a path from anywhere else must not assume the worker will refuse it.
    """
    env = dict(os.environ)
    if forge_dir:
        env["TRVS_FORGE_DIR"] = forge_dir
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = _PACKAGE_ROOT + (
        os.pathsep + existing if existing else "")
    return env


def _encode_request(source: str) -> bytes:
    """The one request envelope, bounded before a process is even started.

    The parent used to do an unbounded ``json.dumps({"source": source})`` on
    bytes the *candidate* wrote. That is a whole copy of a candidate-controlled
    string, plus JSON escaping, materialized in the evaluating process before
    anything could refuse it — so a WRL file large enough to matter was a memory
    problem here, in the process holding the episode, and not in the disposable
    worker.

    Bounding the source first means the refusal costs one ``len`` on bytes that
    were already resident, and the worker is never spawned at all. Its own
    `forge_worker.read_request` applies the same two bounds from the other side;
    that is not redundancy, it is each end refusing what it will not hold.
    """
    encoded = len(source.encode("utf-8", "surrogatepass"))
    if encoded > execlimits.MAX_IPC_SOURCE_BYTES:
        raise ForgeSourceTooLarge(
            "refusing to lower a source of %d bytes; the bound is %d (%s)"
            % (encoded, execlimits.MAX_IPC_SOURCE_BYTES,
               execlimits.EXECUTION_LIMITS_VERSION))
    return _bjson.dump_json_bounded(
        {"source": source}, sort_keys=True,
        max_output_bytes=execlimits.MAX_IPC_REQUEST_BYTES)


def _lower_in_worker(source: str, forge_dir: Optional[str],
                     timeout: float) -> LowerResult:
    """Run one lowering in a killable worker process. Never hangs, never crashes.

    Raises ``ForgeTimeout`` if the worker outlived ``timeout`` (it is killed
    first, with its whole group), ``ForgeSourceTooLarge`` if the request is over
    the IPC bound, and ``ForgeUnavailable`` if it died, exited non-zero, or
    answered with anything other than one parseable in-bounds response object.
    Returns a ``LowerResult`` in exactly the two cases where the engine gave a
    verdict.

    **The capture is bounded, not just the clock.** ``proc.communicate`` read the
    worker's stdout and stderr with no cap, so an engine that printed
    indefinitely — or a lowering pass that dumped a multi-megabyte diagnostic —
    was an unbounded allocation in the evaluating process, reached without the
    timeout ever firing. ``execlimits.run_bounded`` caps both streams as they are
    read and kills the whole group on either a deadline or an overflow.
    """
    try:
        request = _encode_request(source)
    except _bjson.BoundedJsonError as exc:
        raise ForgeSourceTooLarge(
            "the lowering request could not be encoded in bounds: %s" % exc)

    run = execlimits.run_bounded(
        [sys.executable, "-m", _WORKER_MODULE],
        cwd=_PACKAGE_ROOT, env=_worker_env(forge_dir), timeout=timeout,
        input=request,
        max_stdout_bytes=execlimits.MAX_IPC_RESPONSE_BYTES,
        max_stderr_bytes=execlimits.MAX_IPC_DIAGNOSTIC_BYTES)

    if run["spawn_error"] is not None:
        raise ForgeUnavailable(
            "could not start the lowering worker: %s" % run["spawn_error"])

    err = run["stderr"].decode("utf-8", "replace")
    if run["timed_out"]:
        raise ForgeTimeout(
            "the Forge lowering worker did not finish and was killed")
    if run["stdout_truncated"]:
        # More response than the profile will hold is not a response. Refusing
        # it here rather than parsing the prefix is the same rule the worker
        # applies to its own output: a truncated document read as a whole one is
        # how a lowering that never happened gets reported as one that did.
        raise ForgeUnavailable(
            "the Forge lowering worker response exceeded %d bytes"
            % execlimits.MAX_IPC_RESPONSE_BYTES)
    if run["exit_code"] != 0:
        # Includes the shapes no `except` clause could ever have seen in-process:
        # a segfault (negative return code) and an `os._exit`. `exit_code` is
        # `None` when the group was killed for emitting more than the profile
        # retains, which lands here for the same reason.
        raise ForgeUnavailable(
            "the Forge lowering worker exited %s: %s"
            % (run["exit_code"], err.strip()[:512]))
    try:
        response = _bjson.load_json_bounded(
            run["stdout"], max_input_bytes=execlimits.MAX_IPC_RESPONSE_BYTES)
        status = response["status"]
    except Exception as exc:  # noqa: BLE001 -- no parseable answer is no answer
        raise ForgeUnavailable(
            "the Forge lowering worker gave no usable response (%s): %s"
            % (type(exc).__name__, err.strip()[:512]))

    if status != "ok":
        raise ForgeUnavailable(str(response.get("error")))
    if not response.get("ok"):
        return LowerResult(semantic_id=None, ok=False,
                           error=response.get("error"))
    return LowerResult(semantic_id=response.get("semantic_artifact_id"), ok=True)


def _engine_directory(forge_api) -> Optional[str]:
    """Where the parent's engine lives, so the worker can bind the same one.

    ``None`` for an engine object with no ``__file__`` — a test double, in
    practice. The worker then resolves the engine the way it would have anyway;
    a caller that wants a *double* to answer should inject an adapter through
    ``wiring.default_registry(adapter=…)``, which does not cross this boundary at
    all. Stated because a double that silently became the real engine here would
    be a test proving something other than what it says.
    """
    path = getattr(forge_api, "__file__", None)
    if not isinstance(path, str) or not path:
        return None
    return os.path.dirname(os.path.abspath(path))


def real_adapter(forge_api=None, *,
                 timeout: float = FORGE_LOWER_TIMEOUT_SECONDS
                 ) -> ForgeIdentityAdapterV1:
    """Return an adapter bound to the public Forge entrypoint, or raise.

    `forge_api` is the **already selected** engine -- the same object the caller
    used to pack or reopen the environment. Passing it makes the binding explicit:
    the identity verifier then re-lowers through exactly the checkout the rest of
    the command used, instead of independently discovering a possibly different
    one. It is only when no engine is supplied that the soft loader
    (`engine.try_load`) is consulted, so an absent/incompatible engine stays a
    catchable ``ForgeUnavailable`` -- eval-one runs under ``needs_engine=False``,
    so the engine is not pre-loaded and must not ``SystemExit`` here.

    Raises ``ForgeUnavailable`` if the engine cannot be found or exposes no
    callable ``lower_source``; callers that require identity should catch this and
    report the verifier as ``error``.

    **The lowering itself runs in a killable worker process** (route 6). The
    engine is still imported *here*, in the parent, because the version string
    below is read off the module and that string enters ``episode-…``; what moved
    is the one call that takes candidate-controlled bytes as input. An import is
    operator-controlled and is not the hazard; ``lower_source(patched[rel])`` is,
    and it had no timeout and no boundary of any kind.

    ``timeout`` is a **host-resource bound and is deliberately absent from**
    ``version``. Two operators running different timeouts do not mint different
    ids for the same episode — which is the right answer, because the outcome the
    timeout changes is a lowering that *never finished*, and a lowering that never
    finished produced no episode to move. Putting it in the version would move
    every already-sealed ``episode-…`` in exchange for recording an operator knob
    that says nothing about the world being lowered. It is exposed as a keyword
    argument so a law can prove the kill with a short deadline instead of a long
    one; nothing reads it from a task, and a task cannot set it.
    """
    from . import engine

    if forge_api is None:
        forge_api = engine.try_load()
    if forge_api is None:
        raise ForgeUnavailable(
            "could not locate a compatible Forge engine (set TRVS_FORGE_DIR)"
        )
    lower = getattr(forge_api, "lower_source", None)
    if not callable(lower):
        raise ForgeUnavailable("forge_api has no callable lower_source")

    # The identity impl version embeds the WHOLE lowering boundary, not just the
    # engine build, so any change along the path that produced a re-lowered id moves
    # verifier_versions.identity (and thus the episode id): the engine API version
    # (`@api-…`), the frozen LowerResultV1 contract version (`@lower-…`), and the
    # engine build (`@engine-…`). A drift in any layer is an honestly different
    # identity verifier — e.g. `forge.identity.v1@api-1@lower-forge.lower-result.v1
    # @engine-0.7.0-alpha.5`.
    engine_version = getattr(forge_api, "BENCH_VERSION", None)
    if engine_version is None:
        try:
            engine_version = forge_api.engine_info().get("bench_version")
        except Exception:
            engine_version = "unknown"
    api_version = getattr(forge_api, "ENGINE_API_VERSION", "unknown")
    lower_version = getattr(forge_api, "LOWER_RESULT_VERSION", "unknown")
    impl_version = (
        "forge.identity.v1"
        "@api-" + str(api_version)
        + "@lower-" + str(lower_version)
        + "@engine-" + str(engine_version).lstrip("v")
    )

    forge_dir = _engine_directory(forge_api)

    class _RealForgeAdapter(ForgeIdentityAdapterV1):
        version = impl_version

        def lower_source(self, source: str) -> LowerResult:
            # Was `lower(source)` in this process. `lower` is still resolved
            # above — the check that it is callable is part of what makes this
            # adapter bindable at all — but it is no longer *called* here, and
            # deliberately so: `source` is the patched tree's bytes, which the
            # candidate wrote.
            return _lower_in_worker(source, forge_dir, timeout)

    return _RealForgeAdapter()
