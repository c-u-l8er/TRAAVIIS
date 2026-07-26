"""Canonical ``execution_facts`` v1 — the run-environment facts that enter episode-.

GPT-5.6 ruling E1: replace the ad-hoc flat facts dict with a **versioned,
structured** object. Only normalized values enter ``episode-…`` — hostnames,
absolute paths, PIDs, and wall-clock timestamps never do.

Schema (``residency.execution-facts.v1``)::

    {
      "execution_facts_version": "residency.execution-facts.v1",
      "runner":   {"profile": "residency.trusted-local.v1"},
      "platform": {"os": "linux", "arch": "x86_64"},
      "toolchain": {"profile": "...", "resolved": {"python": {"version": "...",
                    "executable_digest": "sha256:..."}}},
      "agent_process": {"termination": "exited"|"timed_out",
                        "exit_code": <int|null>,
                        "stdout_truncated": <bool>, "stderr_truncated": <bool>},
      "sandbox": {"filesystem": "observed"|"enforced",
                  "network": "unrestricted"|"enforced"}
    }

Sandbox labels are **honest** (GPT-5.6 R2/E2): the current runner *observes*
filesystem writes by rescan and does **not** enforce a real sandbox, and network
is unrestricted. The label reflects the actual guarantee, never an aspirational
one. The sandbox story is owned by the runner profile (see ``RUNNER_PROFILES``).

**Not every profile launches a process.** The ORS submission adapter
(``traaviis.ors-submission.v1``, GPT-5.6 ruling seven) scores a *submitted*
finding + patch: no agent command is spawned, so there is no sandbox to describe
and no exit code to report. Such profiles are listed in
``NON_EXECUTING_PROFILES`` and seal ``termination:"not_executed"`` with a
``not_applicable`` sandbox. That is the honest label — *not* a fabricated
``exited``/``0``, which would claim a process ran.
"""

from typing import Any, Mapping, Optional

__all__ = [
    "EXECUTION_FACTS_VERSION",
    "RUNNER_PROFILES",
    "NON_EXECUTING_PROFILES",
    "ORS_RUNNER_PROFILE",
    "UnsupportedPolicyError",
    "validate_run_policy",
    "normalize_platform",
    "build_execution_facts",
]

EXECUTION_FACTS_VERSION = "residency.execution-facts.v1"

#: The declared adapter profile for remote submissions (GPT-5.6 ruling seven).
ORS_RUNNER_PROFILE = "traaviis.ors-submission.v1"

# Each runner profile states the sandbox guarantee it actually delivers.
#   filesystem "observed"  — writes are rescanned + reported, NOT blocked.
#   network    "unrestricted" — no network isolation is applied.
# A future hardened profile may raise these to "enforced" once real isolation
# exists; until then the trusted-local profile is the only honest option.
#
# NOTE FOR ANY FUTURE EDIT: ``build_execution_facts`` seals
# ``sandbox = dict(RUNNER_PROFILES[profile])`` straight into ``episode-…``.
# Adding a *key* here is additive and moves nothing. Adding a *field to an
# existing profile's dict* would move every episode identity ever minted under
# that profile. The ORS profile is therefore a new key, never a new field.
RUNNER_PROFILES = {
    "residency.trusted-local.v1": {
        "filesystem": "observed",
        "network": "unrestricted",
    },
    ORS_RUNNER_PROFILE: {
        "filesystem": "not_applicable",
        "network": "not_applicable",
    },
}

#: Profiles under which **no process is launched at all**.
#:
#: Under these the submission arrives already made: the adapter scores a finding
#: and a patch it was handed. A run policy describing a subprocess is vacuous
#: (there is no subprocess to constrain), there is no exit code to compare
#: against ``allowed_exit_codes``, and the sandbox posture is not a weaker
#: guarantee but an inapplicable question.
NON_EXECUTING_PROFILES = frozenset({ORS_RUNNER_PROFILE})

class UnsupportedPolicyError(Exception):
    """A run policy asks for a guarantee the runner profile does not deliver.

    The trusted-local runner is a process runner, not a sandbox: it observes
    filesystem writes by rescan and applies no network isolation. A policy — the
    agent's ``agent_run_policy`` *or* a test plan's ``run_policy`` — that requests
    a stronger posture (e.g. ``network:"disabled"``) or names a different runner
    profile is demanding an enforcement this runner does not provide. Accepting it
    would seal a dishonest sandbox label / run an isolation-free command under a
    label that claims isolation, so it is rejected at preflight.
    """


def validate_run_policy(policy: Mapping[str, Any], runner_profile: str) -> None:
    """Assert ``policy`` only requests what ``runner_profile`` actually delivers.

    The single shared capability check for **both** the agent run policy and the
    ``tests`` verifier's run policy (they share one honest trusted-local runner, so
    they must share one honesty gate). A policy may name ``runner_profile`` and
    ``network`` only if they equal the active profile's real guarantees; either may
    be omitted (the profile default applies). Any stronger or mismatched request
    raises ``UnsupportedPolicyError`` before the command runs.
    """
    caps = RUNNER_PROFILES.get(runner_profile)
    if caps is None:
        raise UnsupportedPolicyError(f"unknown runner profile {runner_profile!r}")
    if runner_profile in NON_EXECUTING_PROFILES:
        # Nothing is launched, so there is no command whose posture the policy
        # could describe. Checking a subprocess policy against a profile that
        # runs no subprocess would reject honest configurations for a guarantee
        # that is not merely weaker but inapplicable. (The ``tests`` verifier
        # still validates *its own* test-plan policy against the local profile
        # it actually executes under — see substrate_verifiers.)
        return
    requested_profile = policy.get("runner_profile")
    if requested_profile is not None and requested_profile != runner_profile:
        raise UnsupportedPolicyError(
            f"policy names runner_profile={requested_profile!r} but this run uses "
            f"{runner_profile!r}"
        )
    requested_net = policy.get("network")
    supported_net = caps["network"]
    if requested_net is not None and requested_net != supported_net:
        raise UnsupportedPolicyError(
            f"runner profile {runner_profile!r} is a trusted-local process runner, "
            f"not a sandbox: it cannot honor network={requested_net!r} (it provides "
            f"only network={supported_net!r}). Declare network={supported_net!r} + "
            f"runner_profile={runner_profile!r} for an honest run."
        )


_OS_ALIASES = {"linux": "linux", "darwin": "darwin", "macos": "darwin",
               "win32": "windows", "windows": "windows"}
_ARCH_ALIASES = {"x86_64": "x86_64", "amd64": "x86_64",
                 "aarch64": "arm64", "arm64": "arm64"}


def normalize_platform(platform: Any) -> dict:
    """Normalize a platform descriptor into ``{"os", "arch"}``.

    Accepts a ``{"os", "arch"}`` mapping or a ``"<os>-<arch>"`` string. Unknown
    tokens pass through lowercased rather than being dropped, so identity is stable
    but never silently loses information.
    """
    if isinstance(platform, Mapping):
        os_raw = str(platform.get("os", "unknown"))
        arch_raw = str(platform.get("arch", "unknown"))
    elif isinstance(platform, str) and "-" in platform:
        os_raw, arch_raw = platform.split("-", 1)
    else:
        os_raw, arch_raw = str(platform), "unknown"
    os_norm = _OS_ALIASES.get(os_raw.lower(), os_raw.lower())
    arch_norm = _ARCH_ALIASES.get(arch_raw.lower(), arch_raw.lower())
    return {"os": os_norm, "arch": arch_norm}


def build_execution_facts(
    run: Mapping[str, Any],
    *,
    runner_profile: str,
    platform: Any,
    toolchain: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Assemble the canonical ``execution_facts`` object for a run result.

    ``run`` is a ``runner.RunResult``. ``toolchain`` is
    ``{"profile", "resolved": {...}}`` supplied by the caller (the toolchain
    resolver owns it). Raises ``KeyError`` on an unknown ``runner_profile`` — the
    sandbox label must never be fabricated.
    """
    if runner_profile not in RUNNER_PROFILES:
        raise KeyError(f"unknown runner profile: {runner_profile!r}")
    sandbox = dict(RUNNER_PROFILES[runner_profile])

    tc = dict(toolchain or {})
    toolchain_facts = {
        "profile": tc.get("profile"),
        "resolved": dict(tc.get("resolved", {})),
    }

    if runner_profile in NON_EXECUTING_PROFILES:
        # No process was launched. "exited" + exit_code 0 would be a fabrication:
        # it asserts a program ran and succeeded. "not_executed" + null says the
        # true thing, and the replay in episode_bundle reconstructs exactly this.
        agent_process = {
            "termination": "not_executed",
            "exit_code": None,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
    else:
        termination = "timed_out" if run.get("timed_out") else "exited"
        agent_process = {
            "termination": termination,
            "exit_code": run.get("exit_code"),
            "stdout_truncated": bool(run.get("stdout_truncated")),
            "stderr_truncated": bool(run.get("stderr_truncated")),
        }

    return {
        "execution_facts_version": EXECUTION_FACTS_VERSION,
        "runner": {"profile": runner_profile},
        "platform": normalize_platform(platform),
        "toolchain": toolchain_facts,
        "agent_process": agent_process,
        "sandbox": sandbox,
    }
