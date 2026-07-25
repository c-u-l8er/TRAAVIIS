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


def real_adapter(forge_api=None) -> ForgeIdentityAdapterV1:
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

    class _RealForgeAdapter(ForgeIdentityAdapterV1):
        version = impl_version

        def lower_source(self, source: str) -> LowerResult:
            try:
                payload = lower(source)
            except Exception as exc:  # engine/import failure surfaced mid-call
                raise ForgeUnavailable(str(exc)) from exc
            if not payload.get("ok"):
                # Ordinary invalid WRL: a data outcome, not an engine failure.
                return LowerResult(
                    semantic_id=None, ok=False, error=payload.get("error")
                )
            return LowerResult(
                semantic_id=payload["semantic_artifact_id"], ok=True
            )

    return _RealForgeAdapter()
