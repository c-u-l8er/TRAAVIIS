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
  ``real_adapter()``          binds to the public Forge entrypoint if present, else
                              raises ``ForgeUnavailable`` (→ identity verifier
                              ``error``, never a false ``pass``/``fail``).

**Remaining real-Forge dependency (flag for GPT-5.6):** ``real_adapter`` looks for
a public ``forge_api.lower_source``. That public function does not yet exist in the
TRVM tree (only the private ``spinner_bench`` internals). Until it is published,
the real path honestly reports ``ForgeUnavailable``; the stub proves the verifier
law end-to-end.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "LowerResult", "ForgeUnavailable",
    "ForgeIdentityAdapterV1", "StubForgeAdapter", "real_adapter",
]


class ForgeUnavailable(Exception):
    """The Forge re-lower engine could not be reached / is not published yet."""


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


def real_adapter() -> ForgeIdentityAdapterV1:
    """Return an adapter bound to the public Forge entrypoint, or raise.

    Raises ``ForgeUnavailable`` if ``forge_api.lower_source`` is not importable —
    which is the current state of the TRVM tree (the public function is not yet
    published). Callers that require it should catch this and report the identity
    verifier as ``error``.
    """
    try:
        import forge_api  # type: ignore
    except ImportError as exc:
        raise ForgeUnavailable(
            "public forge_api.lower_source is not published yet"
        ) from exc

    lower = getattr(forge_api, "lower_source", None)
    if not callable(lower):
        raise ForgeUnavailable("forge_api has no callable lower_source")

    class _RealForgeAdapter(ForgeIdentityAdapterV1):
        version = "forge.identity-adapter.real.v1"

        def lower_source(self, source: str) -> LowerResult:
            try:
                sem = lower(source)
            except Exception as exc:  # a lowering/compile failure, not engine-down
                return LowerResult(semantic_id=None, ok=False, error=str(exc))
            return LowerResult(semantic_id=str(sem), ok=True)

    return _RealForgeAdapter()
