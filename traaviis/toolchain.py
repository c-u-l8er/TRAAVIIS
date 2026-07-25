"""Toolchain resolution for ``TestPlanV2`` — logical tool name → resolved executable.

A ``TestPlanV2`` command references its interpreter by a **logical** name
(``"python3"``) under a named **toolchain profile** (``"residency.python-host.v1"``)
instead of baking an absolute path (``"/usr/bin/python3"``) into the command. The
logical reference is what enters task identity, so ``task-…`` is **host-independent**:
the same task recognizes as the same task on any machine, regardless of where the
interpreter happens to live. This closes the ``TestPlanV1`` defect where an absolute
argv token in ``test_plan`` made ``task-…`` (and therefore ``episode-…``) machine-specific.

The **resolved** executable — its normalized ``--version`` string and its binary
``executable_digest`` — is an attested *run fact*. It is recorded in
``execution_facts.toolchain.resolved`` (and thus enters ``episode-…`` like every other
execution fact), never in task identity. So the split is:

    task-…      the logical question           (portable across hosts)
    episode-…   answer under a concrete toolchain (its version + binary digest seal)

On replay ``verify_episode_bundle`` carries the stored ``toolchain`` facts verbatim
(``_reconstruct_execution_facts``): the resolved digest is re-hashed from the saved
bytes, not re-resolved, so a saved episode re-closes on any machine. The ``tests``
verifier still re-resolves the tool locally to *re-run* the commands and confirm the
pass/fail verdict — but the sealed digest is never compared against the replay host's
binary.

Resolution is real I/O — locate the executable, read its version, hash its bytes — so
it lives here, outside the pure ``identity`` and ``execfacts`` modules.
"""

import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "TOOLCHAIN_RESOLVER_VERSION",
    "TOOLCHAIN_PROFILES",
    "ToolchainError",
    "ResolvedTool",
    "resolve_tool",
    "resolve_toolchain",
]

# The resolver implementation version. It is not itself sealed into an artifact id;
# it names the resolution contract (which logical tools a profile admits and how a
# resolved tool's version/digest are derived) for doctor/diagnostic reporting.
TOOLCHAIN_RESOLVER_VERSION = "residency.toolchain-resolver.v1"

# Toolchain profile → the set of logical tool names it admits. A profile is a
# closed whitelist: a command naming any tool outside its profile is an
# inadmissible fixture (``ToolchainError``), never a silent host-tool lookup.
TOOLCHAIN_PROFILES: Dict[str, frozenset] = {
    "residency.python-host.v1": frozenset({"python3", "python"}),
}

# Version-probe timeout — a hung ``<exe> --version`` is a resolution failure, not a
# hang. The probe runs the resolved interpreter with a fixed, argument-only flag.
_VERSION_TIMEOUT_SECONDS = 15


class ToolchainError(Exception):
    """A logical tool could not be resolved under its declared profile.

    Raised for an unknown profile, a tool the profile does not admit, a tool that
    is not present on this host, or a version probe that failed. The ``tests``
    verifier maps this to ``error`` (an inadmissible fixture), never to a verdict
    against the agent.
    """


@dataclass(frozen=True)
class ResolvedTool:
    """One resolved logical tool.

    ``executable`` is the absolute host path used to actually run the command — it
    is **host-specific** and is deliberately NOT sealed anywhere. ``version`` (the
    trimmed first ``--version`` line) and ``executable_digest`` (sha256 of the
    binary bytes) are the portable-enough facts recorded in ``execution_facts``.
    """

    tool: str
    executable: str
    version: str
    executable_digest: str

    def facts(self) -> Dict[str, str]:
        """The sealed subset — version + digest, never the host path."""
        return {"version": self.version, "executable_digest": self.executable_digest}


def _digest_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _probe_version(executable: str) -> str:
    try:
        proc = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolchainError(
            "could not probe version of %r: %s" % (executable, type(exc).__name__))
    text = proc.stdout.decode("utf-8", errors="replace")
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not line:
        raise ToolchainError("empty --version output from %r" % executable)
    return line


def _locate(tool: str) -> Optional[str]:
    # ``shutil.which`` uses the resolver process's PATH (trvs itself), never the
    # sealed subprocess env — the resolved *absolute* path is what the runner uses,
    # so the runner's stripped PATH is irrelevant. As a last resort ``python3`` /
    # ``python`` fall back to the interpreter currently running trvs, which is
    # guaranteed present and is the honest "python this host provides".
    exe = shutil.which(tool)
    if exe is None and tool in ("python3", "python"):
        exe = sys.executable
    return exe


def resolve_tool(profile: str, tool: str) -> ResolvedTool:
    """Resolve one logical ``tool`` under ``profile`` to a ``ResolvedTool``.

    Raises ``ToolchainError`` for an unknown profile, a tool the profile does not
    admit, a tool not present on this host, or a failed version probe.
    """
    admitted = TOOLCHAIN_PROFILES.get(profile)
    if admitted is None:
        raise ToolchainError("unknown toolchain profile %r" % profile)
    if tool not in admitted:
        raise ToolchainError(
            "toolchain profile %r does not admit tool %r" % (profile, tool))
    executable = _locate(tool)
    if not executable:
        raise ToolchainError("tool %r not found on this host" % tool)
    return ResolvedTool(
        tool=tool,
        executable=executable,
        version=_probe_version(executable),
        executable_digest=_digest_file(executable),
    )


def resolve_toolchain(
    profile: str, tools: Iterable[str],
) -> Tuple[Dict[str, object], Dict[str, str]]:
    """Resolve every logical tool in ``tools`` under ``profile``.

    Returns ``(facts, executables)`` where ``facts`` is the sealed
    ``execution_facts.toolchain`` object ``{"profile", "resolved": {tool:
    {version, executable_digest}}}`` and ``executables`` maps each tool to its host
    absolute path (used to build argv, never sealed). Tools are resolved in sorted
    order so ``facts`` is deterministic. Raises ``ToolchainError`` on the first tool
    that cannot be resolved.
    """
    resolved: Dict[str, Dict[str, str]] = {}
    executables: Dict[str, str] = {}
    for tool in sorted(set(tools)):
        rt = resolve_tool(profile, tool)
        resolved[tool] = rt.facts()
        executables[tool] = rt.executable
    return {"profile": profile, "resolved": resolved}, executables
