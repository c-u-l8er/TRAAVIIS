"""``SignalIDV1`` — the frozen grammar for verifier signal identifiers.

A signal id names a verifier throughout an episode: it is a ``RewardSpecV1``
signal key, a ``verifier_plan`` entry, an ``extra_verifiers`` / ``live_verifiers``
key, a receipt ``verification`` / ``verification_evidence`` / ``verifier_versions``
key, and — critically — the stem of the on-disk evidence file
``evidence/verifiers/<signal>.json``. An unconstrained signal name is therefore a
filesystem-write primitive: a name carrying ``..`` or ``/`` segments would let
``episode_bundle._populate`` write *outside* the staging directory. This module
freezes a narrow identifier grammar and is validated at every admission boundary,
before any of those uses, and every dynamic evidence path is additionally built
through ``paths.safe_join``.

Pure string law: no filesystem access.
"""

import re

__all__ = [
    "SIGNAL_ID_VERSION",
    "SIGNAL_ID_RE",
    "SignalIDError",
    "is_signal_id",
    "validate_signal_id",
    "validate_signal_ids",
]

SIGNAL_ID_VERSION = "traaviis.signal-id.v1"

# A signal id is a lowercase snake_case token: it must start with ``a-z`` and may
# continue with ``a-z``, ``0-9``, or ``_``, up to 64 characters total. This admits
# every existing signal (``citations``, ``patch``, ``tests``, ``identity``,
# ``finding_completeness``, ``native``, ``oracle``) and rejects anything with a
# path separator, ``..``, drive letter, whitespace, or uppercase.
SIGNAL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SignalIDError(Exception):
    """A signal identifier did not match the frozen ``SignalIDV1`` grammar."""


def is_signal_id(value) -> bool:
    """True iff ``value`` is a string matching the frozen grammar."""
    return isinstance(value, str) and SIGNAL_ID_RE.match(value) is not None


def validate_signal_id(value, *, where: str = "signal id") -> str:
    """Return ``value`` if it is a valid signal id, else raise ``SignalIDError``."""
    if not is_signal_id(value):
        raise SignalIDError(
            "%s is not a valid SignalIDV1 (^[a-z][a-z0-9_]{0,63}$): %r"
            % (where, value))
    return value


def validate_signal_ids(values, *, where: str = "signal id") -> None:
    """Validate every id in ``values``; raise on the first invalid one."""
    for value in values:
        validate_signal_id(value, where=where)
