"""Pure Residency verifiers over sealed evidence (RFC Evidence Residency §6).

Each verifier is a **pure function** of the snapshot content plus the agent's
outputs, returning exactly one of the four states
(``pass | fail | not_applicable | error`` — see ``traaviis.reward``). No
subprocess, no network, no LLM: every signal is a deterministic function over
frozen bytes (§7).

This module implements the three substrate-*independent* verifiers:

  ``citations``            every finding citation resolves in the snapshot and the
                           quote matches the normalized source span exactly.
  ``patch``                the candidate diff applies cleanly to a fresh copy of
                           the snapshot content.
  ``finding_completeness`` the finding carries the required structured fields and
                           evidence coverage — never prose quality (§7).

The remaining two Residency signals are deferred to later slices because they
touch substrate: ``tests`` needs the controlled runner (subprocess) and
``identity`` needs the Forge re-lower (TRVM). Per §10a a **missing or malformed
agent output is a ``fail``**, never an ``error``; ``error`` is reserved for
substrate unavailability, of which these pure verifiers have none.
"""

from typing import Any, Mapping

from . import reward
from .patchapply import PatchError, apply_unified_diff
from .vcontext import VerifierContextV1, VerifierResult

__all__ = [
    "verify_citations", "verify_patch", "verify_finding_completeness",
    "citations_verifier", "patch_verifier", "finding_completeness_verifier",
    "CITATIONS_VERIFIER_VERSION", "PATCH_VERIFIER_VERSION",
    "FINDING_VERIFIER_VERSION",
    "CITATIONS_IMPL_VERSION", "PATCH_IMPL_VERSION",
    "FINDING_IMPL_VERSION",
]

PASS = reward.PASS
FAIL = reward.FAIL

# Contract versions — the *signal* the reward asks for (sealed as ``contract``).
CITATIONS_VERIFIER_VERSION = "residency.citations.v1"
PATCH_VERIFIER_VERSION = "residency.patch.v1"
FINDING_VERIFIER_VERSION = "residency.finding.v1"

# Implementation versions — the *code* that answered (sealed as ``implementation``
# via ``.version``). Contract vs implementation are named independently so a
# reimplementation that keeps the contract still moves ``verifier_versions`` and
# hence ``episode-`` (GPT-5.6 verifier-version-naming ruling).
CITATIONS_IMPL_VERSION = "traaviis.citations-impl.v1"
PATCH_IMPL_VERSION = "traaviis.patch-impl.v1"
FINDING_IMPL_VERSION = "traaviis.finding-completeness-impl.v1"


def _content_lines(text: str) -> list:
    ended_nl = text.endswith("\n")
    lines = text.split("\n")
    if ended_nl:
        lines = lines[:-1]
    return lines


def verify_citations(finding: Mapping[str, Any], content: Mapping[str, str]) -> str:
    """`pass` iff every citation resolves and its quote matches the source span.

    ``content`` maps snapshot relpath -> normalized (LF) text. A citation is
    ``{path, start_line, end_line, quote}`` with 1-based inclusive line ranges
    (§5a). Any missing field, unknown path, out-of-range span, or quote mismatch
    is a ``fail`` (malformed/unsupported output, never an ``error``).
    """
    claims = finding.get("claims")
    if not isinstance(claims, list) or not claims:
        return FAIL
    for claim in claims:
        citations = claim.get("citations") if isinstance(claim, Mapping) else None
        if not isinstance(citations, list) or not citations:
            return FAIL
        for cit in citations:
            if not isinstance(cit, Mapping):
                return FAIL
            path = cit.get("path")
            start = cit.get("start_line")
            end = cit.get("end_line")
            quote = cit.get("quote")
            if not (isinstance(path, str) and isinstance(start, int)
                    and isinstance(end, int) and isinstance(quote, str)):
                return FAIL
            if path not in content:
                return FAIL
            if start < 1 or end < start:
                return FAIL
            lines = _content_lines(content[path])
            if end > len(lines):
                return FAIL
            span = "\n".join(lines[start - 1:end])
            if span != quote:
                return FAIL
    return PASS


def verify_patch(patch: Mapping[str, Any], content: Mapping[str, str]) -> str:
    """`pass` iff the candidate diff applies exactly to a fresh copy of ``content``.

    A missing/malformed diff or any inexact application is a ``fail`` (§10a),
    never an ``error``.
    """
    diff = patch.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        return FAIL
    try:
        apply_unified_diff(content, diff)
    except PatchError:
        return FAIL
    return PASS


def verify_finding_completeness(finding: Mapping[str, Any]) -> str:
    """`pass` iff the finding has the required structured fields + evidence.

    Structural only (§7): at least one claim, each with a non-empty ``statement``
    and at least one citation carrying ``path`` + a valid 1-based inclusive span
    + a ``quote``. Never judges prose quality.
    """
    claims = finding.get("claims")
    if not isinstance(claims, list) or not claims:
        return FAIL
    for claim in claims:
        if not isinstance(claim, Mapping):
            return FAIL
        statement = claim.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            return FAIL
        citations = claim.get("citations")
        if not isinstance(citations, list) or not citations:
            return FAIL
        for cit in citations:
            if not isinstance(cit, Mapping):
                return FAIL
            path = cit.get("path")
            start = cit.get("start_line")
            end = cit.get("end_line")
            quote = cit.get("quote")
            if not (isinstance(path, str) and path
                    and isinstance(start, int) and isinstance(end, int)
                    and isinstance(quote, str)):
                return FAIL
            if start < 1 or end < start:
                return FAIL
    return PASS


# --- Context-based wrappers (uniform verifier interface, §closure step 10) -----
#
# Every verifier — pure or substrate — is called by the orchestrator as
# ``verifier(context) -> VerifierResult``. These three adapt the pure functions
# above to that seam. A missing agent output (``finding``/``patch`` is ``None``)
# is a ``fail`` (§10a), never an ``error``.


def citations_verifier(context: VerifierContextV1) -> VerifierResult:
    """`citations` over a ``VerifierContextV1`` (finding absent → fail)."""
    finding = context.finding
    if finding is None:
        return VerifierResult(FAIL, {"reason": "no finding"})
    return VerifierResult(verify_citations(finding, context.original_content))


def patch_verifier(context: VerifierContextV1) -> VerifierResult:
    """`patch` over a ``VerifierContextV1`` (patch absent → fail)."""
    patch = context.patch
    if patch is None:
        return VerifierResult(FAIL, {"reason": "no patch"})
    return VerifierResult(verify_patch(patch, context.original_content))


def finding_completeness_verifier(context: VerifierContextV1) -> VerifierResult:
    """`finding_completeness` over a ``VerifierContextV1`` (finding absent → fail)."""
    finding = context.finding
    if finding is None:
        return VerifierResult(FAIL, {"reason": "no finding"})
    return VerifierResult(verify_finding_completeness(finding))


# Each wired verifier carries the version of the implementation that produced the
# state (``.version``). The orchestrator seals this beside the reward's declared
# contract as ``{contract, implementation}`` — the contract says *which* signal was
# asked for, ``.version`` says *what code* answered (GPT-5.6 verifier-version ruling).
citations_verifier.version = CITATIONS_IMPL_VERSION
patch_verifier.version = PATCH_IMPL_VERSION
finding_completeness_verifier.version = FINDING_IMPL_VERSION
