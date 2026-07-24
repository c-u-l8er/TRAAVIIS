"""``VerifierContextV1`` — the single, frozen argument every verifier receives.

GPT-5.6 flagged the extra-verifier interface as inconsistent (docs described
``(patched_content, task)`` while the code called ``(run, task, content)``). This
freezes **one** context object and **one** call shape::

    verifier(context: VerifierContextV1) -> VerifierResult

A verifier is a pure-ish function of the sealed subject plus the agent's outputs.
The context carries everything a substrate or pure verifier can legitimately read:

  ``task``              the ``TaskSpecV1`` (verifier plans, policies, test plan…)
  ``snapshot``          the sealed ``SnapshotV1`` subject
  ``original_content``  the subject text map (== the snapshot, post-admission)
  ``finding``           the parsed ``FindingV1`` (or ``None``)
  ``patch``             the parsed ``PatchV1`` (or ``None``)
  ``patched_content``   ``original_content`` with the candidate patch applied, or
                        ``None`` if there was no patch / it did not apply
  ``run``               the ``runner.RunResult`` (trace, exit, policy_violations…)

A ``VerifierResult`` is the four-state string plus optional canonical evidence
detail (kept minimal in v1; a state-only verifier returns ``VerifierResult(state)``).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

__all__ = ["VerifierContextV1", "VerifierResult"]


@dataclass(frozen=True)
class VerifierContextV1:
    """Everything a verifier may read about one episode, in one immutable object."""

    task: Mapping[str, Any]
    snapshot: Mapping[str, Any]
    original_content: Mapping[str, str]
    run: Mapping[str, Any]
    finding: Optional[Mapping[str, Any]] = None
    patch: Optional[Mapping[str, Any]] = None
    patched_content: Optional[Mapping[str, str]] = None


@dataclass(frozen=True)
class VerifierResult:
    """A verifier's four-state verdict plus optional canonical evidence detail."""

    state: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # convenience for callers that only want the state
        return self.state
