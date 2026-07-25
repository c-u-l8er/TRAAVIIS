"""`VerifierRegistryV1` -- which verifier implementations this runtime actually has.

The earlier version of this module wired verifiers from the task alone and
soft-loaded a Forge engine on the spot. That conflated two different things, and
the GPT-5.6 exact-closure ruling separates them:

    declared verifier plan      = f(task)
    available implementations   = f(task, registry / runtime context)
    sealed historical verifiers = receipt.verifier_versions

The distinction is load-bearing. Which signals a task *requires* is a property of
the task bytes, so it is identical everywhere and `declared_signals` computes it
without touching a runtime. But whether the `identity` verifier can answer depends
on which Forge checkout is installed -- that is emphatically not a function of task
bytes, and pretending otherwise is how a library caller silently re-lowers against a
*different* engine than the one that packed the environment and still calls the two
runs comparable.

So the registry is explicit and is built once, from an explicitly selected engine,
by whoever owns the runtime context (the CLI, or a library caller). It is then
handed to `eval-one`, `eval`, and `verify-episode` alike. Caller choice can no
longer silently omit a declared verifier; runtime availability is explicit, and it
is identity-bearing through `verifier_versions`.

Nothing here fakes a result. A declared verifier with no available implementation
is left **unwired** and reported as a note; the eval-one config preflight then
reports the required signal as invalid-config rather than inventing a pass.
"""

__all__ = [
    "VERIFIER_REGISTRY_VERSION",
    "VerifierRegistryV1",
    "default_registry",
    "declared_signals",
    "wire_verifiers",
]

VERIFIER_REGISTRY_VERSION = "traaviis.verifier-registry.v1"


class VerifierRegistryV1:
    """The verifier implementations available in one runtime context.

    Construct it once per process (or per test) and pass it down. An absent
    signal means "this runtime cannot answer it", which is a reportable fact --
    never silently the same as "the task did not ask for it".
    """

    registry_version = VERIFIER_REGISTRY_VERSION

    def __init__(self, *, tests=None, identity=None, notes=()):
        impls = {"tests": tests, "identity": identity}
        self._impls = {k: v for k, v in impls.items() if v is not None}
        #: Why a signal is missing, in human terms. Carried, never fatal.
        self.notes = list(notes)

    def get(self, signal):
        """The implementation for `signal`, or None if this runtime lacks it."""
        return self._impls.get(signal)

    def available(self):
        """The signals this runtime can answer, sorted."""
        return sorted(self._impls)

    def versions(self):
        """`{signal: implementation version}` -- what a receipt would seal."""
        return {k: getattr(v, "version", None) for k, v in sorted(self._impls.items())}

    def __repr__(self):
        return "VerifierRegistryV1(%s)" % ", ".join(self.available())


def default_registry(engine=None, *, adapter=None):
    """Build the registry for the selected runtime context.

    `engine` is the *already selected* Forge API object -- the same one used to
    pack or reopen the environment. It is passed through explicitly so the
    identity verifier cannot bind to a different checkout than the rest of the
    command. When it is None the soft loader is consulted once, here, rather than
    separately (and possibly differently) at each call site.

    `adapter` overrides the identity adapter outright, which is how the battery
    injects a deterministic stub.
    """
    from . import substrate_verifiers as SV
    from . import forge_adapter as FA

    notes = []
    identity_impl = None
    if adapter is not None:
        identity_impl = SV.make_identity_verifier(adapter)
    else:
        try:
            identity_impl = SV.make_identity_verifier(FA.real_adapter(engine))
        except FA.ForgeUnavailable as exc:
            notes.append("identity verifier unavailable: %s" % exc)
    return VerifierRegistryV1(tests=SV.tests_verifier, identity=identity_impl,
                              notes=notes)


def declared_signals(task):
    """The verifier signals a task's own configuration calls for.

    A pure function of the task document -- no runtime, no registry, no I/O. Two
    callers holding the same task bytes always compute the same plan.
    """
    signals = []
    if isinstance(task.get("test_plan"), dict):
        signals.append("tests")
    if isinstance(task.get("identity_policy"), dict):
        signals.append("identity")
    return signals


def wire_verifiers(task, registry=None):
    """Return `(extra_verifiers, notes)` -- the declared plan met by the registry.

    The plan comes from the task; the implementations come from the registry. A
    declared signal the registry cannot supply is reported as a note and left
    unwired, so the preflight can call it invalid-config rather than pass it.

    `registry` defaults to `default_registry()` for convenience, but a caller
    that has an engine in hand should build the registry once and pass it, so
    that every command in the run binds the same implementations.
    """
    if registry is None:
        registry = default_registry()
    extra, notes = {}, []
    for signal in declared_signals(task):
        impl = registry.get(signal)
        if impl is None:
            notes.append("%s verifier unavailable" % signal)
            continue
        extra[signal] = impl
    # Registry-level notes (e.g. why Forge could not be reached) are only worth
    # repeating when the task actually declared the signal they explain.
    if "identity" in declared_signals(task) and registry.get("identity") is None:
        notes.extend(registry.notes)
    return extra, notes
