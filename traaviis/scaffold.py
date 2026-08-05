"""Substrate-aware environment scaffolding for `trvs init` (RFC Artifacts §6).

`init` is **pure environment scaffolding -- not world scaffolding**. It seeds a
subject appropriate to the chosen substrate plus an environment manifest
skeleton, and **invents no identity**. Deriving `env-...`, `snap-...`, `task-...`
and `rew-...` is `pack`'s job, through the §5a admission interface; a scaffold
that shipped a pre-baked id would be asserting a content hash it never computed.

Everything above `materialize()` is pure: `scaffold(template)` maps a template
name to `{relative path -> bytes}` with no clock, no randomness, no host paths
and no filesystem access, so the laws below are testable without touching disk.

Templates name a substrate (§6); different templates scaffold genuinely
different subjects, not a renamed copy of one skeleton:

    | template            | substrate profile          | subject seeded                    |
    | ------------------- | -------------------------- | --------------------------------- |
    | golden-spinner      | trvm.world.v1              | a WallRiderLang world source      |
    | evidence-residency  | residency.repository.v1    | a repository snapshot definition  |

Scaffold laws (the mutation battery in `test/test_scaffold.py`):

    L1  invents no identity   no artifact id key, and no id-shaped literal,
                              appears anywhere in the emitted bytes. The literal
                              half fails CLOSED (`id_tokens`): a rung-prefixed
                              token that does not parse as a digest is a
                              violation, not a silence -- with two exemptions
                              that `id_tokens` names and L1g pins, because a
                              guard that also flags `env-vars` gets switched off
    L2  deterministic         scaffolding twice yields byte-identical trees
    L3  substrate-distinct    templates differ in profile AND in seeded subject
    L4  declared versions     every emitted document declares a frozen schema
                              version and its substrate profile
    L5  fail-closed + atomic   a non-empty destination is refused and nothing is
                              written; a mid-write failure leaves no partial tree
    L6  admissible input      the seeded subject is real -- the WRL world lowers
                              to a genuine `sem-...`, and every path the snapshot
                              definition declares exists in the seeded tree
    L7  portable              no absolute or host-specific path in the bytes
"""

import json
import os
import re
import shutil
import tempfile

__all__ = [
    "ScaffoldError",
    "ENV_MANIFEST_VERSION",
    "SNAPSHOT_DEF_VERSION",
    "TEMPLATES",
    "templates",
    "scaffold",
    "materialize",
    "identity_violations",
    "SCAFFOLD_FILE_MODE",
    "ID_RUNGS",
    "id_tokens",
]

# --- frozen schema versions --------------------------------------------------
# `traaviis.environment.v1` is the env manifest of RFC Artifacts §5. The
# scaffold emits it WITHOUT an `env_id`: the manifest is not closed until `pack`
# verifies closure and derives the id from the canonical bytes.
ENV_MANIFEST_VERSION = "traaviis.environment.v1"

# A snapshot *definition* (which paths form the subject, modes, exclusions) is
# not a snapshot: RFC Artifacts §5 names "snapshot definition" as the scaffolded
# residency artifact, and `residency.snapshot.v1` (with computed file hashes and
# `snap-...`) is what recompute_subject_identity produces from it.
SNAPSHOT_DEF_VERSION = "residency.snapshot-def.v1"

#: Every scaffolded file is written at this exact mode rather than at the
#: process umask. All three templates declare `0644` in their snapshot
#: definitions, and §5c makes that declaration load-bearing: a subject sealed at
#: `0664` cannot survive its own archive and is refused at pack time.
SCAFFOLD_FILE_MODE = 0o644

TASK_SPEC_VERSION = "traaviis.task.v1"
REWARD_SPEC_VERSION = "traaviis.reward.v1"
TEST_PLAN_VERSION = "traaviis.test-plan.v2"
AGENT_RUN_POLICY_VERSION = "traaviis.agent-run-policy.v1"

WORLD_PROFILE = "forge.world.core.v1"

# Every artifact id field in the ladder (RFC Artifacts §1). A scaffold that
# emits any of these is inventing identity.
IDENTITY_KEYS = frozenset({
    "env_id", "bundle_id", "snapshot_id", "task_id", "reward_id", "episode_id",
    "trace_id", "patch_id", "finding_id", "semantic_artifact_id",
})

# --- the id-literal guard ----------------------------------------------------
#
# Three call sites scan text for artifact ids and refuse what they find:
# `identity_violations` below, `test_eval_split.py`'s E5 and `test_compare.py`'s
# C20. All three used to carry their own `<prefix>-<hex>` regex, which made two
# things possible that a *guard* must not permit.
#
#   1. The rung list was written out three times, so a tenth rung added
#      tomorrow would silently escape all three. It is written once here now,
#      and `test_scaffold.py`'s L1c derives the same list from `identity.py`'s
#      minting functions and refuses any prefix this tuple has not heard of --
#      so the duplication is closed by a check, not by a convention.
#
#   2. The regexes were **fail-open**. `[0-9a-f]{8,}` matches a well-formed
#      digest and nothing else, so a rung-prefixed token the pattern did not
#      understand -- `snap-abcdef12ZZZ`, `rew-ABCDEF1234567890`, or the same id
#      under any future grammar (`episode-jcs1-<hex>`) -- produced *no match*,
#      and a guard that finds no id reports no violation. A typo and a scheme
#      change would both read as "clean". The guard now recognises the **rung
#      prefix first** and then asks whether the remainder parses; a rung-
#      prefixed token that does not parse is `"malformed"`, which is a violation
#      in its own right rather than silence.
#
# Both directions matter and they are different checks: `"malformed"` catches a
# *known* rung wearing an unrecognised shape, `"unknown"` catches a *new* rung
# minted by something that had no business minting one (there is no
# `evaluation-...` or `compare-...` rung).
#
# What they do NOT do is catch everything. The first version of this guard said
# they did -- "together they leave no token that is both id-shaped and
# unreported" -- and that sentence was false in two directions at once, which is
# why the guard now states its silence instead of denying it (L1g):
#
#   * It was NARROWER than the regex it replaced. A lookbehind refused to start
#     a token after `-` or `.`, so `prev-episode-42d0bb07e5f83e9e` and
#     `run.episode-42d0bb07e5f83e9e` -- both of which the old `\b`-anchored
#     regex reported -- became `[]`. That was a real, well-formed id going
#     completely unreported. It is reported again: position no longer gates the
#     `"id"` verdict, only the `"malformed"` one (see `_classify`).
#
#   * It was WIDER on prose. Every hyphenated phrase whose first word happened
#     to be a rung name -- `env-vars`, `task-oriented`, `patch-apply` -- was
#     reported as a malformed id. A guard that cries wolf is switched off by the
#     next person to trip it, which is fail-open by a slower route.
#
# The discrimination that resolves both is the REMAINDER's shape and the token's
# POSITION, not position alone. `_classify` states the three rules and why each
# one is the shape it is.

#: Every id prefix in the system, written once. The nine ladder rungs of RFC
#: Artifacts §1 (which `identity.py` mints, and which L1c pins against it), plus
#: the TRVM substrate identities `sem-`/`scen-`/`replay-`, which are substrate
#: ids rather than ladder rungs (ARCHITECTURE.md) but are just as much an
#: identity a scaffold must not assert.
#:
#: It lives *here* rather than in `identity.py` because `identity.py` mints ids
#: and never parses one, and because this module already owns the parallel
#: `IDENTITY_KEYS` -- the same ladder named by field instead of by prefix. The
#: two belong side by side. If it is ever moved next to the minters, the import
#: direction is safe in both files: `identity` imports nothing from this
#: package, so `scaffold -> identity` cannot cycle. Either way L1c is what
#: keeps the list true, so the placement is a readability choice and not a
#: correctness one.
ID_RUNGS = (
    "env", "bundle", "snap", "task", "rew", "episode", "trace", "patch",
    "finding", "sem", "scen", "replay",
)

#: The remainder of a well-formed id: lowercase hex, at least
#: `_ID_DIGEST_FLOOR` of it. `identity._id` always emits a full 64-character
#: sha256, but prose and memos abbreviate (`snap-c66198ab…`) and an abbreviated
#: id is still an asserted identity, so the floor is 8 -- the same floor the
#: original regex used, deliberately unchanged. Measured, not assumed: swept over
#: this repository and its build artifacts, the number of literals the original
#: `<rung>-[0-9a-f]{8,}` regex reports and this guard does not is zero.
#:
#: It does a second job in `_classify`: a remainder shorter than this cannot be
#: even an abbreviated id, so it cannot be a *malformed* one either.
_ID_DIGEST_FLOOR = 8
_ID_DIGEST = re.compile(r"[0-9a-f]{%d,}\Z" % _ID_DIGEST_FLOOR)

#: How long a run of characters has to be before it stops being readable as a
#: *word* and starts being readable as an *identity*. It does two jobs, and they
#: are the same judgement seen from two sides:
#:
#:  * a digest this long under an unrecognised prefix is an invented rung
#:    (`evaluation-<32 hex>`), not ordinary hyphenated text (`cpython-3.11`);
#:  * a lowercase-alphabetic segment this long is not an English word, so it
#:    does not qualify for the prose exemption in `_classify`.
#:
#: 16 is the threshold both id batteries already used for the first job; the
#: longest prose segment in this repository's own scaffolded bytes is
#: `completeness` (12), so it has headroom for the second.
_IDENTITY_RUN_FLOOR = 16

#: A maximal run of identifier-ish characters. Runs must *end* on an
#: alphanumeric or underscore, so a trailing `-` or `.` is a separator rather
#: than part of the token: `trvs-bundle-law-` is the tempdir prefix
#: `trvs-bundle-law`, not the `bundle` rung carrying the remainder `law-`.
_ID_RUN = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?")

#: Segments joined by hyphens, every one of them lowercase letters: the shape of
#: ordinary hyphenated English (`one-shot`, `completeness-impl`, `task-oriented`).
_WORDS = re.compile(r"[a-z]+(?:-[a-z]+)*\Z")


def _classify(prefix, remainder, embedded):
    """One token's verdict, or `None` for "this is not an identity claim".

    `embedded` is true when the prefix is not the first segment of its dotted
    name -- when something came before it and that something was a `-` or a `.`.

    Three rules decide whether a *known rung with a remainder that is not a
    digest* is a malformed id or ordinary text. Each is here because dropping it
    reports something real as a violation:

      1. **Length.** A remainder shorter than `_ID_DIGEST_FLOOR` cannot be even
         an abbreviated id, by this guard's own floor. `task-b` -- a real task
         filename in a packed `env.json` -- is not a truncated `task-…`.

      2. **Shape.** A remainder that is lowercase words joined by hyphens, none
         of them long enough to be an identity, is prose: `env-vars`,
         `task-oriented`, `patch-apply`, `sem-ver`, `bundle-path`.

      3. **Position.** An *embedded* rung is a segment of a longer compound
         name unless it carries a real digest. `traaviis.finding-completeness-
         impl.v1` is the frozen verifier version present in every `ComparisonV1`
         this repository produces (it failed `test_compare`'s C20 the first time
         this guard ran); `traaviis-snap-01uu24ob` is a mkdtemp name. Neither is
         an identity claim, and neither is distinguishable from one by shape
         alone once it sits inside a compound.

    Note what position does *not* gate: a well-formed digest is an `"id"`
    wherever it appears, so `prev-episode-<hex>` and `run.episode-<hex>` are
    reported -- which is the coverage the lookbehind this replaced had silently
    traded away.

    Rules 1-3 buy the absence of false positives at a price, and the price is
    stated rather than hidden: see `id_tokens`'s "deliberately not reported"
    paragraph, which L1g pins.
    """
    digest = _ID_DIGEST.match(remainder) is not None
    if prefix in ID_RUNGS:
        if digest:
            return "id"
        if embedded:
            return None
        if len(remainder) < _ID_DIGEST_FLOOR:
            return None
        if _WORDS.match(remainder) and all(
                len(seg) < _IDENTITY_RUN_FLOOR for seg in remainder.split("-")):
            return None
        return "malformed"
    if digest and len(remainder) >= _IDENTITY_RUN_FLOOR:
        return "unknown"
    return None


def id_tokens(text):
    """Every id-shaped token in `text`, as `[(prefix, remainder, kind)]`.

    `kind` is one of:

        ``"id"``         a known rung carrying a well-formed digest -- an id.
                         Reported wherever it appears, including inside a longer
                         name (`prev-episode-<hex>`, `run.episode-<hex>`).
        ``"malformed"``  a known rung whose remainder is *not* a digest but is
                         not readable as ordinary text either. This is the
                         fail-closed case: the token names a rung, so it is
                         making an identity claim, and the claim does not parse.
        ``"unknown"``    an unrecognised prefix carrying a well-formed digest --
                         something minted a rung that is not in `ID_RUNGS`.

    **Deliberately not reported**, and this is a trade, not an oversight:

      * a rung followed by lowercase words of ordinary length -- `env-vars`,
        `task-oriented`, and equally `episode-nonsense`. Nothing in the two
        strings differs: `nonsense` and `oriented` are both eight lowercase
        letters. There is no lexical discrimination between a typo'd id whose
        remainder happens to be word-shaped and a hyphenated English phrase, so
        this guard reports neither and says so. What bounds the silence is that
        a *digest-shaped* remainder is still an `"id"`, an over-long segment
        (>= `_IDENTITY_RUN_FLOOR`) is still `"malformed"`, and any digit or
        uppercase letter takes the token out of the exemption entirely --
        `snap-abcdef12ZZZ`, `rew-ABCDEF1234567890` and `episode-jcs1-<hex>` are
        all reported. A mangled sha256 keeps its digits; an English word does
        not have any.
      * a rung *embedded* in a longer compound name and not carrying a digest
        (`traaviis.finding-completeness-impl.v1`, `traaviis-snap-01uu24ob`).

    Both silences are pinned by L1g, so they are visible in the battery rather
    than only in this docstring, and a future widening has to move a law.

    The single scan is shared by all three call sites so they cannot drift apart
    again. The text is walked as runs of identifier characters rather than by one
    regex because a rung can begin at *any* hyphen inside a run, and a single
    non-overlapping `finditer` lets the first segment swallow the rest: on
    `prev-episode-<hex>` it matches the prefix `prev` with the whole of
    `episode-<hex>` as its remainder, so simply deleting the lookbehind would
    have left that id just as unreported as before.
    """
    out = []
    for run in _ID_RUN.finditer(text):
        for depth, chunk in enumerate(run.group(0).split(".")):
            parts = chunk.split("-")
            for i in range(len(parts) - 1):
                prefix, remainder = parts[i], "-".join(parts[i + 1:])
                if not prefix[:1].isalpha() or not remainder[:1].isalnum():
                    continue
                kind = _classify(prefix, remainder, embedded=(i > 0 or depth > 0))
                if kind is not None:
                    out.append((prefix, remainder, kind))
                    break  # the rest of the chunk is this token's remainder
    return out


class ScaffoldError(Exception):
    """A template is unknown, or a destination refuses to be scaffolded into."""


def _doc(obj):
    """A scaffolded JSON document: stable key order, LF, trailing newline.

    Authored key order (not sorted) -- these are files a human edits next. Order
    is fixed by the literal below, so the bytes stay deterministic (L2).
    """
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _text(s):
    return s.encode("utf-8")


# --- template: golden-spinner (trvm.world.v1) --------------------------------

_GOLDEN_SPINNER_WORLD = """profile forge.world.core.v1

[pulser:p0](every 2){sig_out}
[relay:r0]{sig_in, sig_out}
[spinner:sp](w=16, n=8, rotor=quarter_turn_z, configurable){sig_in, socket}
[orb:ob]{pose}
[pulser:p1](once at 1){sig_out}
[door:d0]{sig_in}

[pulser:p0] --sig--> [relay:r0]
[relay:r0] --sig--> [spinner:sp]
[spinner:sp] --socket--> [orb:ob]
[pulser:p1] --sig--> [door:d0]
"""

_GOLDEN_SPINNER_README = """# golden-spinner -- a `trvm.world.v1` environment

`trvs init` seeded a subject and an environment manifest skeleton. It derived
no identity: `env.json` has no `env_id`, because nothing has verified this
environment's closure yet.

    world.wrl   the subject -- a WallRiderLang world
    env.json    the environment manifest skeleton (traaviis.environment.v1)

The subject is real; the engine can already speak about it:

    trvs id      world.wrl      # its SemanticArtifactID (sem-...)
    trvs inspect world.wrl      # actors, edges, diagnostics
    trvs run     world.wrl      # deterministically fold it into a film strip
    trvs verify  world.wrl      # reference / native / oracle agreement

Editing `world.wrl` moves the world's `sem-...`; moving a node's geometry does
not. `tasks` and `rewards` are empty because task and reward specs for the TRVM
substrate are not yet defined -- an empty set is honest, a fabricated task is
not.
"""

_GOLDEN_SPINNER_ENV = {
    "environment_version": ENV_MANIFEST_VERSION,
    "substrate_profile": "trvm.world.v1",
    "name": "golden-spinner",
    "description": "A WallRiderLang world as the subject of a TRVM environment.",
    "distribution": {
        "entrypoint": "README.md",
        "documentation": ["README.md"],
    },
    "subject": {
        "kind": "wrl_source",
        "path": "world.wrl",
        "world_profile": WORLD_PROFILE,
    },
    "tasks": [],
    "rewards": [],
    "profiles": {},
    "splits": {},
}


def _golden_spinner():
    return {
        "README.md": _text(_GOLDEN_SPINNER_README),
        "world.wrl": _text(_GOLDEN_SPINNER_WORLD),
        "env.json": _doc(_GOLDEN_SPINNER_ENV),
    }


# --- template: evidence-residency (residency.repository.v1) ------------------

_RESIDENCY_MODULE = "return 1\n"

_RESIDENCY_SPEC = """# Residency: module return contract
The module returns a small integer that downstream stages consume.
The frozen world under world/ MUST keep its sealed semantic identity.
"""

_RESIDENCY_FROZEN_WORLD = """profile forge.world.core.v1

[pulser:p0](every 2){sig_out}
[relay:r0]{sig_in, sig_out}
[spinner:sp](w=16, n=8, rotor=quarter_turn_z, configurable){sig_in, socket}
[orb:ob]{pose}

[pulser:p0] --sig--> [relay:r0]
[relay:r0] --sig--> [spinner:sp]
[spinner:sp] --socket--> [orb:ob]
"""

_RESIDENCY_README = """# evidence-residency -- a `residency.repository.v1` environment

`trvs init` seeded a subject and an environment manifest skeleton. It derived
no identity: there is no `snap-...`, no `task-...`, no `rew-...` and no
`env_id`, because nothing has hashed these bytes yet.

    subject/            the subject -- the repository the agent works in
    snapshot_def.json   which paths form the subject (residency.snapshot-def.v1)
    task.json           the task skeleton   (traaviis.task.v1)
    reward.json         the reward skeleton (traaviis.reward.v1)
    env.json            the environment manifest skeleton

`task.json` deliberately carries no `reward_id` and no `subject.snapshot_id`:
those are references to content hashes that only exist once the snapshot
definition and the reward spec have actually been hashed. Filling them in is
`pack`'s job, and `pack` recomputes rather than trusts them.

The identity policy pins `subject/world/frozen.wrl`: an agent may patch
`src/mod.py`, but if it perturbs the frozen world's `sem-...` the identity
signal fails and the reward is capped.
"""

_RESIDENCY_SNAPSHOT_DEF = {
    "snapshot_def_version": SNAPSHOT_DEF_VERSION,
    "substrate_profile": "residency.repository.v1",
    "root": "subject",
    "include": [
        "src/mod.py",
        "spec/residency.md",
        "world/frozen.wrl",
    ],
    "exclusions": [],
    "binary_paths": [],
    "file_modes": {
        "src/mod.py": "0644",
        "spec/residency.md": "0644",
        "world/frozen.wrl": "0644",
    },
    "base_revision": None,
    "visible_config": {},
}

_RESIDENCY_TASK = {
    "task_spec_version": TASK_SPEC_VERSION,
    "substrate_profile": "residency.repository.v1",
    "subject": {
        "snapshot_def": "snapshot_def.json",
    },
    "instructions": {
        "objective": "cite spec/residency.md, patch src/mod.py within the return "
                     "contract, and keep world/frozen.wrl's identity",
    },
    "reward_spec": "reward.json",
    "verifier_plan": {
        "required": [
            "citations",
            "patch",
            "finding_completeness",
            "tests",
            "identity",
        ],
        "not_applicable": [
            "native",
            "oracle",
        ],
    },
    "test_plan": {
        "test_plan_version": TEST_PLAN_VERSION,
        "toolchain_profile": "residency.python-host.v1",
        "commands": [
            {
                "tool": "python3",
                "args": [
                    "-c",
                    "import sys;sys.exit(0 if open('src/mod.py').read().strip() "
                    "in ('return 1','return 2') else 1)",
                ],
                "cwd": ".",
                "timeout_seconds": 30,
            },
        ],
        "baseline": "must_pass",
        "run_policy": {
            "runner_profile": "residency.trusted-local.v1",
            "network": "unrestricted",
        },
    },
    "identity_policy": {
        "must_remain": {
            "world": {
                "path": "world/frozen.wrl",
                "profile": WORLD_PROFILE,
            },
        },
    },
    "termination": {
        "mode": "one_shot",
    },
    "agent_run_policy": {
        "policy_version": AGENT_RUN_POLICY_VERSION,
        "command_mode": "argv",
        "shell": False,
        "network": "unrestricted",
        "timeout_seconds": 30,
        "max_output_bytes": 4194304,
        "environment": {},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    },
}

_RESIDENCY_REWARD = {
    "reward_spec_version": REWARD_SPEC_VERSION,
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations": {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch": {"verifier": "residency.patch.v1", "weight": 0.2},
        "tests": {"verifier": "residency.tests.v1", "weight": 0.3},
        "identity": {"verifier": "residency.identity.v1", "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1", "weight": 0.1},
    },
    "caps": [
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.4},
    ],
    "aggregation": "terminal",
}

_RESIDENCY_ENV = {
    "environment_version": ENV_MANIFEST_VERSION,
    "substrate_profile": "residency.repository.v1",
    "name": "evidence-residency",
    "description": "A repository snapshot as the subject of a Residency environment.",
    "distribution": {
        "entrypoint": "README.md",
        "documentation": ["README.md"],
    },
    "subject": {
        "kind": "repository_snapshot",
        "snapshot_def": "snapshot_def.json",
    },
    "tasks": ["task.json"],
    "rewards": ["reward.json"],
    "profiles": {
        "runner_profile": "residency.trusted-local.v1",
        "toolchain_profile": "residency.python-host.v1",
    },
    "splits": {
        "all": ["task.json"],
    },
}


def _evidence_residency():
    return {
        "README.md": _text(_RESIDENCY_README),
        "env.json": _doc(_RESIDENCY_ENV),
        "snapshot_def.json": _doc(_RESIDENCY_SNAPSHOT_DEF),
        "task.json": _doc(_RESIDENCY_TASK),
        "reward.json": _doc(_RESIDENCY_REWARD),
        "subject/src/mod.py": _text(_RESIDENCY_MODULE),
        "subject/spec/residency.md": _text(_RESIDENCY_SPEC),
        "subject/world/frozen.wrl": _text(_RESIDENCY_FROZEN_WORLD),
    }


# --- template: residency-repair (residency.repository.v1) --------------------
#
# The first task in this repository that is a *repair* rather than a conformance
# check. The difference is not cosmetic, and it is worth being precise about,
# because the difference was mistaken for a distinction without one before.
#
# `evidence-residency` seeds `return 1` and an acceptance test that accepts
# either `return 1` or `return 2`. That test cannot fail on the seeded subject,
# so it never asks whether the agent fixed anything -- it only asks whether the
# agent produced *some* admissible patch. That is a genuine conformance fixture
# for the five-signal contract, and a reviewer correctly reclassified it as one.
# It is not a bug report.
#
# This template states a bug. The spec requires 2; the implementation returns 1;
# the two disagree on the seeded subject, and the acceptance test says so out
# loud by declaring what it expects in *both* phases:
#
#     target test        baseline exits 1   patched exits 0
#     repository health  baseline exits 0   patched exits 0
#
# Under TestPlanV1 that shape was inexpressible. V1 required every baseline
# command to exit 0, so a test that reproduces a bug -- red before the fix, green
# after -- would have been read as a broken fixture and errored out before the
# agent was ever consulted. The per-phase `allowed_exit_codes` of V2 is what
# makes "this test is *supposed* to fail right now" a statement the task can
# make. The second command is the control: it is green in both phases, so a
# candidate that "fixes" the target by breaking the repository is caught rather
# than rewarded.

_REPAIR_MODULE = "return 1\n"

_REPAIR_SPEC = """# Contract: the module returns two
The module's sole statement returns the integer the downstream stage consumes.
That integer is 2. An implementation returning any other value is a defect,
including the value it currently returns.
The frozen world under world/ MUST keep its sealed semantic identity.
"""

_REPAIR_FROZEN_WORLD = _RESIDENCY_FROZEN_WORLD

_REPAIR_README = """# residency-repair -- a real repair task

The subject ships a **defect**: `spec/contract.md` requires the module to return
2 and `src/mod.py` returns 1. The task is to fix it.

    subject/            the subject -- the repository the agent works in
    snapshot_def.json   which paths form the subject (residency.snapshot-def.v1)
    task.json           the task              (traaviis.task.v1)
    reward.json         the reward            (traaviis.reward.v1)
    env.json            the environment manifest skeleton

What makes this a repair task and not a conformance check is the test plan. It
declares two commands and, for each, what exit code it expects **before** and
**after** the candidate patch:

| command | baseline | patched | what it is for |
| --- | --- | --- | --- |
| `target` | exits 1 | exits 0 | reproduces the defect, then proves it fixed |
| `health` | exits 0 | exits 0 | the repository still works either way |

The `target` command is red on the seeded subject *on purpose*. That is the
reproduction. Under `traaviis.test-plan.v1` this could not be written down: a
baseline was required to exit 0, so a failing-before-the-fix test read as a
broken fixture. `traaviis.test-plan.v2` lets each phase declare its own
`allowed_exit_codes`, which is exactly the vocabulary a bug report needs.

The asymmetry between the two phases is deliberate. A baseline that does not
behave as declared means the *sealed subject* is not the repository the task
describes, so it is an inadmissible fixture -- `error`, never a verdict against
an agent who has not been consulted yet. A patched run that misses its
expectation is a verdict against the candidate -- `fail`.

The identity policy pins `subject/world/frozen.wrl`: an agent may patch
`src/mod.py`, but perturbing the frozen world's `sem-...` fails the identity
signal and caps the reward. A repair is only a repair if it leaves everything
else alone.
"""

_REPAIR_SNAPSHOT_DEF = {
    "snapshot_def_version": SNAPSHOT_DEF_VERSION,
    "substrate_profile": "residency.repository.v1",
    "root": "subject",
    "include": [
        "src/mod.py",
        "spec/contract.md",
        "world/frozen.wrl",
    ],
    "exclusions": [],
    "binary_paths": [],
    "file_modes": {
        "src/mod.py": "0644",
        "spec/contract.md": "0644",
        "world/frozen.wrl": "0644",
    },
    "base_revision": None,
    "visible_config": {},
}

# The target: `return 2` and nothing else. Note what it does NOT accept -- the
# seeded `return 1`. That is the entire point; an acceptance test that admits the
# defect it is meant to catch is a conformance check wearing a repair task's
# clothes.
_REPAIR_TARGET_CHECK = (
    "import sys;sys.exit(0 if open('src/mod.py').read().strip() == 'return 2' "
    "else 1)"
)

# The control: the spec is intact and the module is still a single non-empty
# statement. Green in both phases, so "fixing" the target by deleting the spec or
# emptying the module is caught rather than rewarded.
_REPAIR_HEALTH_CHECK = (
    "import sys;"
    "spec=open('spec/contract.md').read();"
    "mod=[l for l in open('src/mod.py').read().split(chr(10)) if l.strip()];"
    "sys.exit(0 if spec.strip() and len(mod) == 1 else 1)"
)

_REPAIR_TASK = {
    "task_spec_version": TASK_SPEC_VERSION,
    "substrate_profile": "residency.repository.v1",
    "subject": {
        "snapshot_def": "snapshot_def.json",
    },
    "instructions": {
        "objective": "src/mod.py returns 1; spec/contract.md requires 2. Cite the "
                     "spec, patch src/mod.py to satisfy it, and keep "
                     "world/frozen.wrl's identity",
    },
    "reward_spec": "reward.json",
    "verifier_plan": {
        "required": [
            "citations",
            "patch",
            "finding_completeness",
            "tests",
            "identity",
        ],
        "not_applicable": [
            "native",
            "oracle",
        ],
    },
    "test_plan": {
        "test_plan_version": TEST_PLAN_VERSION,
        "toolchain_profile": "residency.python-host.v1",
        "commands": [
            {
                "tool": "python3",
                "args": ["-c", _REPAIR_TARGET_CHECK],
                "cwd": ".",
                "timeout_seconds": 30,
                # Red before the fix. Declared, not discovered.
                "baseline": {"allowed_exit_codes": [1]},
                "patched": {"allowed_exit_codes": [0]},
            },
            {
                "tool": "python3",
                "args": ["-c", _REPAIR_HEALTH_CHECK],
                "cwd": ".",
                "timeout_seconds": 30,
                "baseline": {"allowed_exit_codes": [0]},
                "patched": {"allowed_exit_codes": [0]},
            },
        ],
        "baseline": "must_pass",
        "run_policy": {
            "runner_profile": "residency.trusted-local.v1",
            "network": "unrestricted",
        },
    },
    "identity_policy": {
        "must_remain": {
            "world": {
                "path": "world/frozen.wrl",
                "profile": WORLD_PROFILE,
            },
        },
    },
    "termination": {
        "mode": "one_shot",
    },
    "agent_run_policy": {
        "policy_version": AGENT_RUN_POLICY_VERSION,
        "command_mode": "argv",
        "shell": False,
        "network": "unrestricted",
        "timeout_seconds": 30,
        "max_output_bytes": 4194304,
        "environment": {},
        "writable_paths": ["."],
        "result_path": "result.json",
        "patch_path": "candidate.patch",
    },
}

_REPAIR_REWARD = {
    "reward_spec_version": REWARD_SPEC_VERSION,
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations": {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch": {"verifier": "residency.patch.v1", "weight": 0.2},
        "tests": {"verifier": "residency.tests.v1", "weight": 0.3},
        "identity": {"verifier": "residency.identity.v1", "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1", "weight": 0.1},
    },
    "caps": [
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "citations", "state": "fail"}, "reward_max": 0.25},
        {"when": {"signal": "tests", "state": "fail"}, "reward_max": 0.4},
    ],
    "aggregation": "terminal",
}

_REPAIR_ENV = {
    "environment_version": ENV_MANIFEST_VERSION,
    "substrate_profile": "residency.repository.v1",
    "name": "residency-repair",
    "description": "A repository with a real defect: the spec requires 2, the "
                   "implementation returns 1.",
    "distribution": {
        "entrypoint": "README.md",
        "documentation": ["README.md"],
    },
    "subject": {
        "kind": "repository_snapshot",
        "snapshot_def": "snapshot_def.json",
    },
    "tasks": ["task.json"],
    "rewards": ["reward.json"],
    "profiles": {
        "runner_profile": "residency.trusted-local.v1",
        "toolchain_profile": "residency.python-host.v1",
    },
    "splits": {
        "all": ["task.json"],
    },
}


def _residency_repair():
    return {
        "README.md": _text(_REPAIR_README),
        "env.json": _doc(_REPAIR_ENV),
        "snapshot_def.json": _doc(_REPAIR_SNAPSHOT_DEF),
        "task.json": _doc(_REPAIR_TASK),
        "reward.json": _doc(_REPAIR_REWARD),
        "subject/src/mod.py": _text(_REPAIR_MODULE),
        "subject/spec/contract.md": _text(_REPAIR_SPEC),
        "subject/world/frozen.wrl": _text(_REPAIR_FROZEN_WORLD),
    }


# --- registry ----------------------------------------------------------------

TEMPLATES = {
    "golden-spinner": {
        "substrate_profile": "trvm.world.v1",
        "summary": "a WallRiderLang world subject, foldable and verifiable now",
        "build": _golden_spinner,
    },
    "evidence-residency": {
        "substrate_profile": "residency.repository.v1",
        "summary": "a repository snapshot subject with a task and reward skeleton",
        "build": _evidence_residency,
    },
    "residency-repair": {
        "substrate_profile": "residency.repository.v1",
        "summary": "a real defect: the spec requires 2, the implementation "
                   "returns 1, and the target test is red until it is fixed",
        "build": _residency_repair,
    },
}


def templates():
    """`[(name, substrate_profile, summary)]` in stable, listed order."""
    return [(n, t["substrate_profile"], t["summary"]) for n, t in TEMPLATES.items()]


def scaffold(template):
    """Pure: `{relative path -> bytes}` for `template`. No I/O, no identity."""
    entry = TEMPLATES.get(template)
    if entry is None:
        raise ScaffoldError(
            "unknown template: %s\n  known templates: %s"
            % (template, ", ".join(TEMPLATES)))
    return entry["build"]()


def identity_violations(files):
    """L1: paths whose bytes assert an identity the scaffold never computed.

    Returns `[(path, reason)]` -- empty for a lawful scaffold. Checks both the
    structural form (an id *key* in a JSON document, at any depth) and the
    textual form (an id-shaped literal anywhere in the bytes, including prose
    and comments).

    The textual half is **fail-closed** (see `id_tokens`): a rung-prefixed token
    whose remainder is not a hex digest and not readable as ordinary text is
    reported as a malformed id literal rather than passing as "no id here", and
    an unrecognised prefix carrying a digest is reported as an invented rung. A
    scaffold that wants to *talk* about an id writes the elision form
    (`snap-...`), which asserts nothing and is not a token -- and so is ordinary
    hyphenated prose (`set the env-vars first`), which a template is free to
    contain.

    `id_tokens` names the two shapes it deliberately does not report, and L1g
    pins them; this function inherits both silences.
    """
    out = []
    for path in sorted(files):
        data = files[path]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for prefix, remainder, kind in id_tokens(text):
            literal = "%s-%s" % (prefix, remainder)
            if kind == "id":
                out.append((path, "id literal %r" % literal))
            elif kind == "malformed":
                out.append((path, "malformed id literal %r: %r names the %r "
                                  "rung but does not parse as a digest"
                                  % (literal, remainder, prefix)))
            else:
                out.append((path, "id literal %r under the unknown rung %r"
                                  % (literal, prefix)))
        if path.endswith(".json"):
            try:
                doc = json.loads(text)
            except ValueError:
                out.append((path, "not valid JSON"))
                continue
            for key in _walk_keys(doc):
                if key in IDENTITY_KEYS:
                    out.append((path, "identity key %r" % key))
    return out


def _walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            for kk in _walk_keys(v):
                yield kk
    elif isinstance(node, list):
        for v in node:
            for kk in _walk_keys(v):
                yield kk


def materialize(template, dest):
    """Write `template` into `dest` atomically. Returns the paths written.

    L5: refuses a destination that exists and is non-empty, and builds the whole
    tree in a sibling temporary directory before a single `os.replace`, so an
    interrupted `init` never leaves a half-formed environment behind.

    Every file is written at an **explicit** `0644`, never at whatever the
    process umask happens to be. `residency.repository.v1` seals the exact mode
    into `snap-...` and refuses a subject mode that cannot survive canonical
    distribution (§5c), so a scaffold written under `umask 002` would emit an
    environment that `trvs pack` immediately -- and correctly -- refuses. A
    template's modes are part of the template, not of the machine that unpacked
    it.
    """
    files = scaffold(template)  # pure, and raises before touching the filesystem
    dest = os.path.abspath(dest)
    if os.path.exists(dest):
        if not os.path.isdir(dest):
            raise ScaffoldError("destination exists and is not a directory: %s" % dest)
        if os.listdir(dest):
            raise ScaffoldError(
                "refusing to scaffold into a non-empty directory: %s" % dest)
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".trvs-init-", dir=parent)
    try:
        os.chmod(tmp, 0o755)
        for rel in files:
            target = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(files[rel])
            os.chmod(target, SCAFFOLD_FILE_MODE)
        if os.path.isdir(dest):
            os.rmdir(dest)  # empty, checked above
        os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return sorted(files)
