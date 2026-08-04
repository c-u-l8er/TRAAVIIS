"""Laws for `Residency MCP Profile v1` -- the Episode Kernel's second transport.

Built against **MCP specification revision `2026-07-28`**, the release that made
MCP stateless: no `initialize` handshake, no protocol-level sessions, per-request
`_meta`, and server-minted handles passed as ordinary tool arguments.

The first transport (ORS, laws O1-O30) was the test of whether the kernel
extraction produced a *translation layer*. A second transport tests something
the first one structurally could not: whether the layering **generalises**, or
whether `ors.py` had quietly been shaped around HTTP. Every law here is a way
that could have gone wrong.

The failure modes they exist to close:

- the protocol revision could be a remembered one rather than the one in force,
  which is an unfalsifiable claim in exactly the shape this repo refuses
  (M1-M5);
- the stateless core's per-request metadata could be tolerated-when-absent,
  which quietly keeps the deleted handshake's assumptions (M2-M4);
- the caching and result-type fields this revision made mandatory could be
  omitted, or claimed dishonestly -- a long `ttlMs` on something that can change
  (M5, M6);
- the tool catalog could advertise operations the substrate refuses, which over
  HTTP costs a retry loop and over MCP costs a *language model* repeatedly
  calling something that never works (M9-M11);
- the mapping could collapse: a session published as a resource, an episode
  listed as one, or a prompt copied instead of shared (M12-M15);
- the trust boundary could be lost in translation -- `RemoteSubmissionV1` is
  validated by exact key set, and a transport that folded its own handle into
  that document would have had to weaken it (M16-M18);
- idempotency could be re-invented as a tool argument, putting a key in the
  hands of the language model and making double-execution merely *look*
  deduplicated (M19-M21);
- MCP's two error channels mean different things, and putting a server defect on
  the channel a model self-corrects from invites an infinite retry (M22, M23);
- the kernel's linearization could be undone by a read-handle-write loop, which
  preserves the property on paper and loses it in fact (M24-M27);
- and the whole thing could add a rung, a verb, a second admission path, or a
  network surface stdio does not have (M28-M30).

**On what needs an engine.** Nothing here does. Every law runs against injected
verifiers over an in-memory subject, so this file runs identically with or
without a Forge checkout.

Run directly:      python3 test/test_mcp.py
Run under pytest:  pytest test/test_mcp.py
"""

import ast
import hashlib
import inspect
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from traaviis import episode_bundle as EB  # noqa: E402
from traaviis import identity as I  # noqa: E402
from traaviis import kernel as K  # noqa: E402
from traaviis import mcp as M  # noqa: E402
from traaviis import mcp_server as MS  # noqa: E402
from traaviis import ors as O  # noqa: E402
from traaviis import reward as R  # noqa: E402
from traaviis import runner as RUN  # noqa: E402
from traaviis import snapshot as S  # noqa: E402
from traaviis.substrates import AdmissionError  # noqa: E402
from traaviis.vcontext import VerifierResult  # noqa: E402


class Skip(Exception):
    pass


# ---------------------------------------------------------------- fixtures
_TMP = {}


def _tmp():
    if "d" not in _TMP:
        _TMP["d"] = tempfile.mkdtemp(prefix="trvs-mcp-law-")
    return _TMP["d"]


def _out(name):
    path = os.path.join(_tmp(), name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


CONTENT = {"spec/one.md": "alpha\nbeta\n", "src/mod.py": "return 1\n"}

REWARD_SPEC = {
    "reward_spec_version": "traaviis.reward.v1",
    "substrate_profile": "residency.repository.v1",
    "signals": {
        "citations":            {"verifier": "residency.citations.v1", "weight": 0.25},
        "patch":                {"verifier": "residency.patch.v1",     "weight": 0.20},
        "tests":                {"verifier": "residency.tests.v1",     "weight": 0.30},
        "identity":             {"verifier": "residency.identity.v1",  "weight": 0.15},
        "finding_completeness": {"verifier": "residency.finding.v1",   "weight": 0.10},
    },
    "caps": [
        {"when": {"signal": "patch", "state": "fail"}, "reward_max": 0.25},
    ],
    "aggregation": "terminal",
}

TOOLCHAIN = {"profile": "cpython-3.11",
             "resolved": {"python": {"version": "3.11.4"}}}

FINDING = {
    "summary": "spec/one.md line 2 contradicts src/mod.py",
    "citations": [
        {"path": "spec/one.md", "start_line": 2, "end_line": 2, "quote": "beta"}
    ],
}
PATCH = ("--- a/src/mod.py\n+++ b/src/mod.py\n"
         "@@ -1,1 +1,1 @@\n-return 1\n+return 2\n")

ENV_ID = "env-" + "0" * 64


def _submission(finding=FINDING, patch=PATCH):
    return {"submission_version": O.SUBMISSION_VERSION,
            "finding": finding, "patch": patch}


def _content_hash(text):
    data = text.encode("utf-8").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot():
    snap = {"snapshot_version": S.SNAPSHOT_VERSION,
            "files": {k: _content_hash(v) for k, v in CONTENT.items()},
            "exclusions": [], "binary_paths": [], "file_modes": {},
            "base_revision": None, "visible_config": {}}
    snap["snapshot_id"] = I.snapshot_id(snap)
    return snap


def _task():
    return {
        "task_spec_version": "traaviis.task.v1",
        "substrate_profile": "residency.repository.v1",
        "subject": {"snapshot_id": _snapshot()["snapshot_id"]},
        "instructions": {"objective": "demo"},
        "reward_id": I.reward_id(REWARD_SPEC),
        "verifier_plan": {"required": ["citations", "patch", "tests", "identity"],
                          "not_applicable": ["native", "oracle"]},
        "termination": {"mode": "one_shot"},
        "agent_run_policy": {
            "policy_version": "traaviis.agent-run-policy.v1",
            "command_mode": "argv", "shell": False, "network": "unrestricted",
            "timeout_seconds": 30, "max_output_bytes": 4194304,
            # NO ambient `PATH`, deliberately -- this fixture inherited one by
            # copy and it is a latent identity leak even though this battery
            # pins no episode id. `runner._seal_env` (R1) *discards* any
            # caller-supplied `PATH`, so declaring one never reaches a child;
            # but `agent_run_policy` is inside `task-` and `task-` is inside
            # `episode-`, so an ambient `PATH` moves the episode identity while
            # changing nothing the identity describes. Under this transport
            # nothing is executed at all, which makes it pure noise in a name.
            "environment": {"TRAAVIIS_STUB_MODE": "ok"},
            "writable_paths": ["."],
            "result_path": "result.json", "patch_path": "candidate.patch",
        },
    }


def _pass(context):
    return VerifierResult(R.PASS)


_pass.version = "residency.tests.v1"


def _pass_identity(context):
    return VerifierResult(R.PASS)


_pass_identity.version = "residency.identity.v1"

ALL_PASS = {"tests": _pass, "identity": _pass_identity}


def _kernel(extra=ALL_PASS):
    return K.local_kernel(
        _task(), CONTENT, REWARD_SPEC, snapshot=_snapshot(),
        extra_verifiers=extra, platform="linux-x86_64", toolchain=TOOLCHAIN,
        runner_profile=O.ORS_RUNNER_PROFILE)


def _pair(output=None, extra=ALL_PASS):
    """Both adapters over one kernel: the ORS one, and the MCP one over it.

    Returned as a pair because several laws are precisely about the *relationship*
    between them -- that the prompt is shared rather than copied (M15), that the
    submission document is the same document (M16), that no second admission path
    exists (M30).
    """
    kernel = _kernel(extra)
    out = output or _out("episodes-%d" % id(kernel))
    ors_adapter = O.OrsAdapterV1(kernel, env_id=ENV_ID, output=out)
    return ors_adapter, M.McpAdapterV1(ors_adapter, output=out), kernel


def _adapter(output=None, extra=ALL_PASS):
    return _pair(output, extra)[1]


def _meta(version=M.MCP_PROTOCOL_VERSION, caps=None):
    return {M.META_PROTOCOL_VERSION: version,
            M.META_CLIENT_CAPABILITIES: {} if caps is None else caps,
            M.META_CLIENT_INFO: {"name": "trvs-law", "version": "1"}}


def _request(method, params=None, ident=1, meta=None):
    params = dict(params or {})
    params["_meta"] = _meta() if meta is None else meta
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}


_IDS = [100]


def _call(adapter, method, params=None, meta=None):
    """One request through the adapter, with a fresh id each time."""
    _IDS[0] += 1
    return adapter.handle(_request(method, params, ident=_IDS[0], meta=meta))


def _tool(adapter, name, arguments=None):
    return _call(adapter, "tools/call",
                 {"name": name, "arguments": arguments or {}})


def _result(response):
    assert "error" not in response, "expected a result, got %s" % response["error"]
    return response["result"]


def _error(response):
    assert "error" in response, "expected an error, got %s" % response.get("result")
    return response["error"]


def _open(adapter, task_id=None):
    if task_id is None:
        task_id = _result(_tool(adapter, "list_tasks"))["structuredContent"]["tasks"][0]
    out = _result(_tool(adapter, "open_session", {"task_id": task_id}))
    return out["structuredContent"]["session_id"]


def _submit(adapter, session_id, submission=None):
    return _tool(adapter, "submit_candidate",
                 {"session_id": session_id,
                  "submission": submission or _submission()})


def _episode(adapter):
    """Open, submit once, return the structured result of a scored episode."""
    return _result(_submit(adapter, _open(adapter)))["structuredContent"]


def _code_identifiers(source):
    """Every *code* identifier in `source`: names, attributes, imports, args.

    String constants and docstrings are excluded by construction, because they
    are not identifiers. This battery inherits a rule the K- and O-batteries
    each had to learn the hard way (K10, K12, K18, K28, O30): **a module is
    allowed to name a seam it deliberately does not cross**, so a law about a
    seam has to parse rather than grep. `mcp_server`'s docstring explains at
    length that it has no socket and no port; a text scan would read those very
    sentences as evidence that it does.
    """
    names = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.update(node.module.split("."))
            for alias in node.names:
                names.update(alias.name.split("."))
                if alias.asname:
                    names.add(alias.asname)
    return names


def _refusal_codes(source, classname):
    """Every code a `classname(...)` in `source` is raised with, parsed and literal."""
    codes = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != classname:
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
            "a %s code must be a literal, not a computed value" % classname
        codes.add(first.value)
    return sorted(codes)


def _law_names():
    return sorted(k for k, v in globals().items()
                  if k.startswith("test_m") and callable(v))


class _Live(object):
    """A really-running stdio server on a real pair of pipes.

    Not a convenience. Several laws are about the *transport* rather than the
    adapter -- that responses are framed one per line, that two requests can be
    in flight at once, that EOF is the shutdown signal -- and none of those is
    observable through `McpAdapterV1.handle`.
    """

    def __init__(self, adapter):
        client_to_server = os.pipe()
        server_to_client = os.pipe()
        self._server_in = os.fdopen(client_to_server[0], "rb")
        self._client_out = os.fdopen(client_to_server[1], "wb")
        self._client_in = os.fdopen(server_to_client[0], "rb")
        self._server_out = os.fdopen(server_to_client[1], "wb")
        self.server = MS.McpStdioServer(
            adapter, stdin=self._server_in, stdout=self._server_out)
        self.raw = []
        self._by_id = {}
        self._cv = threading.Condition()
        self._serve = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self._serve.start()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        while True:
            line = self._client_in.readline()
            if not line:
                break
            with self._cv:
                self.raw.append(line)
                try:
                    message = json.loads(line.decode("utf-8"))
                except ValueError:
                    message = {"id": None, "_unparseable": True}
                self._by_id[_key(message.get("id"))] = message
                self._cv.notify_all()

    def send(self, message):
        data = json.dumps(message).encode("utf-8") + b"\n"
        self._client_out.write(data)
        self._client_out.flush()

    def request(self, method, params=None, ident=None, meta=None):
        _IDS[0] += 1
        ident = _IDS[0] if ident is None else ident
        self.send(_request(method, params, ident=ident, meta=meta))
        return self.await_id(ident)

    def await_id(self, ident, timeout=60):
        with self._cv:
            ok = self._cv.wait_for(lambda: _key(ident) in self._by_id,
                                   timeout=timeout)
            assert ok, "no response for request %r within %ss" % (ident, timeout)
            return self._by_id[_key(ident)]

    def has_id(self, ident):
        with self._cv:
            return _key(ident) in self._by_id

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self._client_out.close()   # EOF: the shutdown signal the spec names
        except OSError:
            pass
        self._serve.join(timeout=30)
        try:
            self._server_out.close()
        except OSError:
            pass
        self._reader.join(timeout=10)


def _key(ident):
    return MS._hashable(ident)


class _HeldScoring(object):
    """Hold `_finish_episode` open, and count how many callers reach it.

    The same device the K-battery used, for the same reason: a concurrency law
    that spawns two threads and *hopes* they collide passes for the wrong reason
    most of the time. Holding the scoring work open makes the interleaving
    certain, so the law fails every run against a transport that serializes and
    passes every run against one that does not.

    The counter is the point. "Exactly one submission succeeded" is strictly
    weaker than "the work ran once" -- the defect the linearization slice fixed
    satisfied the first while violating the second.
    """

    def __init__(self, hold=True):
        self.entered = threading.Semaphore(0)
        self.release = threading.Event()
        self.count = 0
        self._lock = threading.Lock()
        self._hold = hold
        self._real = None

    def __enter__(self):
        self._real = K._finish_episode

        def wrapper(*args, **kwargs):
            with self._lock:
                self.count += 1
            self.entered.release()
            if self._hold:
                self.release.wait(timeout=30)
            return self._real(*args, **kwargs)

        K._finish_episode = wrapper
        return self

    def __exit__(self, *exc):
        self.release.set()
        K._finish_episode = self._real


class _Barrier(object):
    """Force two *different* sessions to be inside the scoring work at once.

    A transport that serialized requests would **deadlock** this rather than
    merely run slowly, which is what makes it a proof and not a timing
    observation. It is the M-battery's copy of K25's argument, moved up to the
    wire.
    """

    def __init__(self, parties=2):
        self.barrier = threading.Barrier(parties, timeout=30)
        self.broken = []
        self._real = None

    def __enter__(self):
        self._real = K._finish_episode

        def wrapper(*args, **kwargs):
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError as exc:  # pragma: no cover
                self.broken.append(exc)
            return self._real(*args, **kwargs)

        K._finish_episode = wrapper
        return self

    def __exit__(self, *exc):
        K._finish_episode = self._real


# =========================================================== M1-M5: the wire
def test_m1_the_protocol_revision_is_named_and_is_the_one_in_force():
    """The server states which MCP revision it speaks, and speaks only that.

    A transport that does not name its protocol revision is making an
    unfalsifiable claim: "it supports MCP" cannot be wrong, because there is no
    stated thing to check it against. `2026-07-28` is the revision that removed
    the `initialize` handshake and protocol-level sessions, so the difference
    between it and a remembered earlier one is not cosmetic -- a server built
    against the wrong one is wrong about its entire lifecycle.
    """
    assert M.MCP_PROTOCOL_VERSION == "2026-07-28"
    assert M.MCP_SUPPORTED_VERSIONS == ("2026-07-28",), \
        "a server must not list a revision it has not implemented"

    result = _result(_call(_adapter(), "server/discover"))
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["_meta"][M.META_SERVER_INFO]["name"] == "traaviis"

    # Capabilities are declared honestly *empty*. The catalog is frozen at
    # admission and nothing can add to it while the process lives, so there is
    # no `listChanged` this server could ever emit -- and declaring one it never
    # sends is the same false claim as listing a tool that always refuses.
    caps = result["capabilities"]
    assert set(caps) == {"tools", "resources", "prompts"}
    assert caps["tools"] == {} and caps["prompts"] == {}
    assert caps["resources"] == {}, \
        "no subscribe / listChanged is claimed, because none is implemented"
    assert result["instructions"], "a DiscoverResult should orient a model"


def test_m2_every_request_must_carry_the_stateless_cores_metadata():
    """`protocolVersion` and `clientCapabilities` are required on every request.

    This is the load-bearing consequence of `2026-07-28`. The handshake could be
    deleted *because* these fields ride on each request; a server that accepts
    their absence has silently kept the handshake's assumptions without the
    handshake, and will behave differently the first time a client reconnects
    mid-conversation. The spec's answer is `-32602`, and it is a MUST.
    """
    adapter = _adapter()

    no_meta = adapter.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                              "params": {}})
    assert _error(no_meta)["code"] == M.ERR_INVALID_PARAMS

    for missing in (M.META_PROTOCOL_VERSION, M.META_CLIENT_CAPABILITIES):
        meta = _meta()
        del meta[missing]
        response = _call(adapter, "tools/list", meta=meta)
        assert _error(response)["code"] == M.ERR_INVALID_PARAMS, \
            "%s is required on every request" % missing
        assert missing in json.dumps(_error(response)), \
            "the refusal must name the field that is missing"

    # `clientInfo` is only SHOULD, so its absence is not a refusal.
    meta = _meta()
    del meta[M.META_CLIENT_INFO]
    assert "error" not in _call(adapter, "tools/list", meta=meta)


def test_m3_an_unsupported_revision_is_refused_with_the_fallback_a_client_needs():
    """A version mismatch is `-32022`, and it says what *is* supported.

    `UnsupportedProtocolVersionError` is not merely an error code; it is the
    mechanism by which a client selects a mutually supported revision and
    retries. An error that refused without listing `supported` would leave a
    dual-era client with no way forward except guessing, and the spec is
    explicit that a recognised modern error is how a client learns the server is
    modern at all.
    """
    response = _call(_adapter(), "tools/list", meta=_meta(version="1900-01-01"))
    error = _error(response)
    assert error["code"] == M.ERR_UNSUPPORTED_PROTOCOL_VERSION == -32022
    assert error["data"]["supported"] == ["2026-07-28"]
    assert error["data"]["requested"] == "1900-01-01"

    # A version that is a real MCP revision but not this one is refused the same
    # way. "Nearly right" is not a category this check has.
    older = _call(_adapter(), "tools/list", meta=_meta(version="2025-11-25"))
    assert _error(older)["code"] == -32022


def test_m4_there_is_no_handshake_and_no_request_depends_on_a_previous_one():
    """Statelessness, tested as a property rather than asserted as a comment.

    Two halves. `initialize` is gone, so it is an unknown method -- and a server
    that answered it would be claiming legacy semantics it does not implement.
    And every RPC works as the *first* thing a client ever sends: a brand-new
    adapter answers `tools/call` with no discovery, no listing and no priming.
    """
    adapter = _adapter()
    assert _error(_call(adapter, "initialize"))["code"] == M.ERR_METHOD_NOT_FOUND
    assert _error(_call(adapter, "notifications/initialized"))["code"] \
        == M.ERR_METHOD_NOT_FOUND

    # A cold adapter, first message is a scoring tool call, and it works.
    fresh = _adapter()
    task_id = fresh._adapter.list_tasks()[0]
    first = _result(_tool(fresh, "open_session", {"task_id": task_id}))
    assert first["structuredContent"]["session_id"].startswith(K.SESSION_PREFIX)


def test_m5_every_result_carries_resultType_and_serverInfo():
    """`resultType` became required in this revision; `serverInfo` is a SHOULD.

    `resultType` is what lets a client tell a finished answer from an
    `input_required` interim one without guessing from shape. This server never
    returns `input_required` -- it asks the client for nothing -- so every result
    is `complete`, and saying so on every result is what makes that a checkable
    statement rather than an omission a client has to assume its way past.
    """
    adapter = _adapter()
    session_id = _open(adapter)
    responses = [
        _call(adapter, "server/discover"),
        _call(adapter, "tools/list"),
        _call(adapter, "resources/list"),
        _call(adapter, "resources/templates/list"),
        _call(adapter, "prompts/list"),
        _tool(adapter, "list_tasks"),
        _tool(adapter, "close_session", {"session_id": session_id}),
    ]
    for response in responses:
        result = _result(response)
        assert result["resultType"] == "complete", result
        assert result["_meta"][M.META_SERVER_INFO] == M.SERVER_INFO

    # Including on a tool *execution error*: `isError` lives in a result, so a
    # refusal is still a complete result, not an incomplete one.
    refused = _result(_tool(adapter, "open_session", {"task_id": "task-nope"}))
    assert refused["isError"] is True
    assert refused["resultType"] == "complete"


# ================================================== M6: caching, honestly claimed
def test_m6_cacheable_results_declare_ttl_and_scope_and_earn_them():
    """`ttlMs` and `cacheScope` are required here, and both must be *true*.

    A freshness hint is a claim about how long an answer stays right. The
    catalogs earn a long one structurally: `open_adapter` freezes the served set
    before anything is served. An episode earns one for a stronger reason --
    content addressing makes staleness impossible, because a different receipt
    would have a different name.

    `cacheScope` is a different question from `ttlMs`, and the two are answered
    differently on purpose. Tasks are the published benchmark and are `public`;
    an episode holds a submitter's finding and patch and is `private`. Marking
    an episode `public` would invite a shared intermediary to keep somebody
    else's candidate.
    """
    adapter = _adapter()
    for method, params in (("tools/list", None), ("resources/list", None),
                           ("resources/templates/list", None),
                           ("prompts/list", None)):
        result = _result(_call(adapter, method, params))
        assert result["ttlMs"] == M.TTL_CATALOG_MS
        assert result["cacheScope"] == "public", method

    scored = _episode(adapter)
    episode = _result(_call(adapter, "resources/read",
                            {"uri": scored["episode_uri"]}))
    assert episode["cacheScope"] == "private", \
        "an episode carries a submitter's candidate; it is not shared-cacheable"
    assert episode["ttlMs"] == M.TTL_EPISODE_MS

    task_uri = _result(_call(adapter, "resources/list"))["resources"][1]["uri"]
    assert _result(_call(adapter, "resources/read",
                         {"uri": task_uri}))["cacheScope"] == "public"


def test_m7_a_notification_gets_no_response_ever():
    """JSON-RPC forbids a response to a notification; so does MCP.

    Worth a law rather than trust, because the natural shape of a dispatcher is
    "compute an answer, write it" and the id check is the one line that stops a
    notification from producing a stray message. On stdio a stray message is not
    a nuisance -- it is a line the client will try to correlate against a request
    that does not exist.
    """
    adapter = _adapter()
    for method in ("notifications/cancelled", "notifications/progress",
                   "notifications/anything"):
        message = _request(method, {})
        del message["id"]
        assert adapter.handle(message) is None, method

    with _Live(_adapter()) as live:
        live.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": 999}})
        live.request("tools/list")     # a real request, to order the stream
        assert all(json.loads(l.decode())["id"] is not None for l in live.raw), \
            "a notification produced a line on the wire"


def test_m8_an_unknown_method_is_method_not_found():
    """`-32601`, and not a tool error.

    A method name is not something a language model chooses -- the client library
    does -- so an unknown method is not the sort of thing a model can
    self-correct. It belongs on the protocol channel, which is exactly the line
    M22 draws for everything else.
    """
    adapter = _adapter()
    for method in ("tools/frobnicate", "resources/subscribe", "ping",
                   "logging/setLevel"):
        assert _error(_call(adapter, method))["code"] == M.ERR_METHOD_NOT_FOUND, \
            method

    # `resources/subscribe` and `ping` are named above deliberately: both were
    # *removed* in this revision. A server that still answered them would be
    # speaking a revision it does not advertise.


# ==================================== M9-M11: the catalog is a set of claims
def test_m9_the_tool_catalog_is_exactly_four_tools_in_a_fixed_order():
    """Four tools, deterministic order, and every schema closed.

    `2026-07-28` asks for a deterministic order so clients can cache and so an
    LLM's prompt cache hits. A tuple makes that structural rather than a
    property of whatever the dict iteration happened to do.

    Every `inputSchema` is `additionalProperties: false`. An open schema on the
    submission tool would advertise that extra keys are acceptable, which is the
    opposite of what `RemoteSubmissionV1` means.
    """
    tools = _result(_call(_adapter(), "tools/list"))["tools"]
    names = [t["name"] for t in tools]
    assert names == list(M.TOOLS)
    assert names == ["list_tasks", "open_session", "submit_candidate",
                     "close_session"]

    for tool in tools:
        assert tool["description"], tool["name"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, tool["name"]
        assert tool["outputSchema"]["type"] == "object", tool["name"]

    submit = tools[2]
    assert sorted(submit["inputSchema"]["properties"]) == \
        ["session_id", "submission"]
    assert submit["inputSchema"]["properties"]["submission"]["required"] == \
        list(O.SUBMISSION_FIELDS)

    # Twice in a row is the same bytes. A catalog that reordered between calls
    # would defeat the caching the revision asks for.
    again = _result(_call(_adapter(), "tools/list"))["tools"]
    assert json.dumps(again, sort_keys=True) == json.dumps(tools, sort_keys=True)


def test_m10_the_refused_operations_are_not_advertised_but_are_still_answered():
    """`observe` / `step` / `reset`: absent from the menu, present at the door.

    Both halves matter and they pull in opposite directions.

    Absent from `tools/list`, because that list is handed to a language model as
    a menu. ORS §2's rule -- an advertised capability is a *claim* -- is sharper
    here than over HTTP: a client's retry loop discovers a bad claim at 3am, but
    a model discovers it immediately and repeatedly, because the menu said the
    operation exists.

    Still answered by `tools/call`, because "unknown tool" is a claim about this
    server's catalog and the truth is a claim about the *substrate*. The refusal
    that comes back is the kernel's own, relayed verbatim -- so on the day a
    substrate learns to step, this transport says so without being edited.
    """
    adapter = _adapter()
    listed = {t["name"] for t in _result(_call(adapter, "tools/list"))["tools"]}
    for operation in K.INTERACTIVE_OPERATIONS:
        assert operation not in listed, \
            "%s must not appear in a menu shown to a model" % operation

        result = _result(_tool(adapter, operation))
        assert result["isError"] is True, operation
        error = result["structuredContent"]["error"]
        assert error["code"] == "KERNEL_OPERATION_UNSUPPORTED", operation
        # In the substrate's words: the message names the substrate and lists
        # what it *does* support, which is what makes it actionable.
        assert "residency.repository.v1" in error["message"], operation
        assert error["detail"]["operation"] == operation
        assert sorted(error["detail"]["supported"]) == \
            sorted(K.ResidencyKernelV1.supported_operations)


def test_m11_a_name_that_is_neither_a_tool_nor_an_operation_is_a_protocol_error():
    """Unknown tool → `-32602`, which is what the spec requires.

    The distinction M10 sets up only means something if the *other* case lands
    somewhere else. A name in `kernel.OPERATIONS` gets the substrate's answer; a
    name in neither the catalog nor that frozen vocabulary gets "unknown tool",
    because that is precisely what it is.
    """
    adapter = _adapter()
    for name in ("frobnicate", "submit", "SUBMIT_CANDIDATE", "tools/call"):
        error = _error(_tool(adapter, name))
        assert error["code"] == M.ERR_INVALID_PARAMS, name
        assert "Unknown tool" in error["message"], name
        assert error["data"]["tools"] == list(M.TOOLS)

    # `list_tasks` is a kernel operation *and* a tool. It resolves as the tool.
    assert "list_tasks" in K.OPERATIONS
    assert _result(_tool(adapter, "list_tasks"))["isError"] is False


# ==================== M12-M15: tools vs resources vs prompts, argued not guessed
def test_m12_resources_are_the_content_addressed_and_immutable():
    """The resource space holds derivations, and reading one twice is identical.

    That is the whole justification for the `trvs://` scheme and for the long
    `ttlMs`: an `env-…` and a `task-…` are names *derived from* the bytes they
    return, so "is my copy stale?" has no interesting answer. If a read could
    return different bytes under the same URI, the scheme would be a location
    scheme wearing a content-addressed one's clothes.
    """
    adapter = _adapter()
    resources = _result(_call(adapter, "resources/list"))["resources"]
    uris = [r["uri"] for r in resources]
    assert uris[0] == "trvs://env/" + ENV_ID
    assert all(u.startswith("trvs://") for u in uris)

    task_ids = adapter._adapter.list_tasks()
    assert uris[1:] == ["trvs://task/%s" % t for t in task_ids]
    for resource in resources:
        assert resource["mimeType"] == "application/json"
        assert resource["name"] and resource["description"]

    for uri in uris:
        first = _result(_call(adapter, "resources/read", {"uri": uri}))
        second = _result(_call(adapter, "resources/read", {"uri": uri}))
        assert first["contents"] == second["contents"], uri
        assert len(first["contents"]) == 1
        assert first["contents"][0]["uri"] == uri
        json.loads(first["contents"][0]["text"])   # it is what it says it is


def test_m13_a_session_is_not_a_resource_and_a_missing_uri_is_invalid_params():
    """A session has none of a resource's properties, so it is not in the space.

    It is mutable, ephemeral, and derived from randomness rather than content --
    `kernel.SESSION_PREFIX` exists so that is visible at a glance. Publishing
    `trvs://session/<id>` would put a thing with none of a resource's properties
    into the namespace whose entire meaning is those properties, and a client
    would reasonably cache it.

    The second half is this revision's renumbering: resource-not-found moved off
    `-32002` onto `-32602` to align with JSON-RPC. And `contents` is never
    returned empty for a missing resource, because an empty list cannot
    distinguish "exists and is empty" from "does not exist".
    """
    adapter = _adapter()
    session_id = _open(adapter)

    listed = json.dumps(_result(_call(adapter, "resources/list")))
    assert session_id not in listed
    assert "session" not in listed.lower(), \
        "the resource space must not name sessions at all"

    for uri in ("trvs://session/%s" % session_id, "trvs://task/task-nope",
                "trvs://env/env-nope", "trvs://", "trvs://episode/",
                "file:///etc/passwd", "trvs://episode/../../etc/passwd",
                "trvs://nonsense/x"):
        error = _error(_call(adapter, "resources/read", {"uri": uri}))
        assert error["code"] == M.ERR_INVALID_PARAMS, uri
        assert error["code"] != -32002, "this revision renumbered not-found"
        assert error["message"] == "Resource not found", uri


def test_m14_episodes_are_reached_by_link_and_template_not_by_listing():
    """A published episode is a resource, but it is not in `resources/list`.

    `resources/list` must not vary as a side effect of other requests, and
    episodes exist *because of* other requests. The spec names the pattern for
    exactly this: a tool returns a `resource_link`, and links returned by tools
    are not guaranteed to appear in a listing. The template advertises the shape
    so a client that has an id from elsewhere can still read it.
    """
    adapter = _adapter()
    before = _result(_call(adapter, "resources/list"))["resources"]
    scored = _episode(adapter)
    after = _result(_call(adapter, "resources/list"))["resources"]
    assert before == after, \
        "scoring an episode must not change the listed resource set"

    templates = _result(_call(adapter,
                              "resources/templates/list"))["resourceTemplates"]
    assert [t["uriTemplate"] for t in templates] == \
        ["trvs://episode/{episode_id}"]

    # The link the tool returned resolves, and resolves to *this* episode.
    receipt = json.loads(_result(_call(adapter, "resources/read",
                                       {"uri": scored["episode_uri"]}
                                       ))["contents"][0]["text"])
    assert receipt["episode_id"] == scored["episode_id"]
    assert receipt["reward"] == scored["reward"]


def test_m15_the_prompt_is_shared_with_the_other_transport_not_copied():
    """One prompt, parameterised, and its text is `ors.task_prompt` byte for byte.

    Two agents given "the same task" must have been given the same task. A
    second copy of the prompt text in this module would be a second thing that
    could drift from the first, and the drift would be invisible: both would
    look like reasonable instructions.

    One parameterised prompt rather than one prompt per task, because the
    *template* does not vary -- only its data does, and data that varies belongs
    in an argument. N copies of one template is a catalog that grows without
    adding meaning.
    """
    ors_adapter, adapter, _kern = _pair()
    prompts = _result(_call(adapter, "prompts/list"))["prompts"]
    assert [p["name"] for p in prompts] == [M.PROMPT_NAME] == ["residency_task"]
    assert prompts[0]["arguments"] == [
        {"name": "task_id", "description": "a task id from list_tasks",
         "required": True}]

    task_id = ors_adapter.list_tasks()[0]
    got = _result(_call(adapter, "prompts/get",
                        {"name": M.PROMPT_NAME, "arguments": {"task_id": task_id}}))
    assert got["messages"][0]["role"] == "user"
    assert got["messages"][0]["content"]["text"] == ors_adapter.task_prompt(task_id)

    for params in ({"name": "nope", "arguments": {"task_id": task_id}},
                   {"name": M.PROMPT_NAME, "arguments": {}},
                   {"name": M.PROMPT_NAME, "arguments": {"task_id": "task-nope"}}):
        assert _error(_call(adapter, "prompts/get", params))["code"] \
            == M.ERR_INVALID_PARAMS, params


# ================================ M16-M18: the trust boundary crosses intact
def test_m16_the_submission_is_a_nested_document_with_its_exact_key_set_intact():
    """The handle rides *outside* the document, so the exact key set survives.

    This is the law the whole design turns on. `RemoteSubmissionV1` is validated
    by exact key set -- not "these are required", but "this is the document".
    Folding `session_id` in beside `finding` and `patch` would have forced a
    fourth allowed key or a strip-then-validate step, and both turn "is this the
    document?" back into "does this contain the document?".

    It is the same argument that put ORS's idempotency key in a header: a thing
    the transport needs is not a thing the document says.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    # A forbidden field is refused **by name**, through this transport, with the
    # ORS vocabulary unchanged -- the boundary is not re-implemented here.
    bad = _submission()
    bad["reward"] = 1.0
    result = _result(_submit(adapter, session_id, bad))
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "ORS_SUBMISSION_FIELD"
    assert "reward" in error["detail"]["forbidden"]

    # The refusal did not spend the handle: nothing ran, so nothing was consumed.
    ok = _result(_submit(adapter, session_id))
    assert ok["isError"] is False and ok["structuredContent"]["reward"] == 1.0

    # A key nobody foresaw is refused too, and a wrong version is caught first.
    session_id = _open(adapter)
    unknown = _submission()
    unknown["notes"] = "hello"
    assert _result(_submit(adapter, session_id, unknown)
                   )["structuredContent"]["error"]["code"] == "ORS_SUBMISSION_FIELD"
    v2 = _submission()
    v2["submission_version"] = "traaviis.remote-submission.v2"
    assert _result(_submit(adapter, session_id, v2)
                   )["structuredContent"]["error"]["code"] == "ORS_SUBMISSION_VERSION"


def test_m17_the_handle_cannot_be_smuggled_into_the_document_or_omitted():
    """`session_id` inside `submission` is refused; outside, it is required.

    The nesting is only a boundary if both sides of it are enforced. A
    `session_id` *inside* the document is an unknown field to the exact key set;
    a missing one *outside* is a tool argument error. Neither silently succeeds,
    which is what stops the two layers from being merged by a well-meaning
    client.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    smuggled = _submission()
    smuggled["session_id"] = session_id
    result = _result(_submit(adapter, session_id, smuggled))
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "ORS_SUBMISSION_FIELD"

    for arguments in ({"submission": _submission()},
                      {"session_id": "", "submission": _submission()},
                      {"session_id": 7, "submission": _submission()}):
        out = _result(_tool(adapter, "submit_candidate", arguments))
        assert out["isError"] is True
        assert out["structuredContent"]["error"]["code"] == "MCP_ARGUMENT_MISSING"

    extra = {"session_id": session_id, "submission": _submission(),
             "idempotency_key": "k1"}
    out = _result(_tool(adapter, "submit_candidate", extra))
    assert out["isError"] is True
    assert out["structuredContent"]["error"]["code"] == "MCP_ARGUMENT_UNKNOWN", \
        "an argument nobody declared must not be silently ignored"


def test_m18_nothing_the_client_sent_determines_the_receipt():
    """The server builds the `RunResult`; the client narrates nothing.

    The receipt this transport produces is sealed under the *non-executing*
    runner profile, because nothing was executed -- exactly as over ORS. If MCP
    had built its own run result, or accepted one, a client could narrate its own
    execution into evidence. This module contains no `RunResult` construction at
    all, which M30 checks structurally; here it is checked from the outside.
    """
    adapter = _adapter()
    scored = _episode(adapter)
    receipt = json.loads(_result(_call(adapter, "resources/read",
                                       {"uri": scored["episode_uri"]}
                                       ))["contents"][0]["text"])

    facts = receipt["execution_facts"]
    assert facts["runner"]["profile"] == O.ORS_RUNNER_PROFILE
    assert facts["agent_process"]["exit_code"] is None, \
        "no process ran, so there is no exit code"
    assert facts["agent_process"]["termination"] == "not_executed", \
        "`not_executed` is not `exited`: nothing ran to exit"
    assert facts["sandbox"]["filesystem"] == "not_applicable"
    assert facts["sandbox"]["network"] == "not_applicable", \
        "`not_applicable` is not `none`: there was no program to sandbox"

    # A null exit code is not read downstream as a failed run: the episode
    # scored, and scored positively.
    assert receipt["status"] == "ok"
    assert receipt["reward"] == scored["reward"] == 1.0

    # The trace says a submission arrived, not that a process ran.
    assert receipt["trace_id"].startswith("trace-")
    assert "mcp" not in json.dumps(receipt).lower(), \
        "the transport must not appear in the evidence it carried"


# ============================== M19-M21: idempotency, refused rather than replayed
def test_m19_a_spent_handle_is_refused_and_the_refusal_is_not_a_dead_end():
    """A second submission is refused -- and told where its episode went.

    The choice: MCP adds **no** idempotency key, because the two wires have
    different retry semantics. HTTP retries happen below the caller, so ORS
    needed a key to stop a machine-generated duplicate becoming a second
    episode. Under `2026-07-28` the opposite is specified -- stream resumability
    and redelivery were removed, and a client whose stream breaks must re-issue
    with a *new* request id. Retries are explicit and already distinguishable.

    So the honest answer is the kernel's own: one caller leaves `_claim_finalize`
    holding the claim, everyone else is refused by name. What refusing costs is
    recoverability, and that is repaired without a cache: the refusal carries the
    `episode_id` and a `trvs://episode/…` link. It is still a refusal -- no
    reward, the handle stays spent -- it simply points at the evidence, which is
    on disk because `finished: true` was only ever returned after it got there.
    """
    adapter = _adapter()
    session_id = _open(adapter)
    first = _result(_submit(adapter, session_id))["structuredContent"]

    second = _result(_submit(adapter, session_id))
    assert second["isError"] is True, "a spent handle must not be re-scored"
    error = second["structuredContent"]["error"]
    assert error["code"] == "ORS_SESSION_FINISHED"
    assert "reward" not in second["structuredContent"], \
        "a refusal must not look like a result"

    # Navigable, not replayed.
    assert error["detail"]["episode_id"] == first["episode_id"]
    assert error["detail"]["episode_uri"] == first["episode_uri"]
    receipt = json.loads(_result(_call(adapter, "resources/read",
                                       {"uri": error["detail"]["episode_uri"]}
                                       ))["contents"][0]["text"])
    assert receipt["episode_id"] == first["episode_id"]

    # And it says what to do next, in words a model can act on.
    assert "new session" in error["message"]


def test_m20_no_idempotency_key_is_invented_anywhere_in_this_transport():
    """No tool takes a key, and the ORS adapter is called with a literal `None`.

    A key as a *tool argument* would be worse than no key. Tool arguments are
    written by a language model, and a key a model invents is not an idempotency
    key -- it is a token that makes double-execution *look* deduplicated. By ORS
    §3's own logic it is also client-supplied content that steers server
    behaviour, which is the category `RemoteSubmissionV1` exists to keep empty.

    Passing `idempotency_key=None` makes ORS's O21 rule ("a keyless second
    submission is refused") the MCP rule, with no new mechanism and no cache.
    The keyword is asserted to be a **literal** `None`, because a computed one
    would make this uncheckable by any means.
    """
    # Checked against the schemas' *property names*, not against the JSON text.
    # The first form of this law scanned the serialized tool for "idempot" and
    # failed on `close_session`, whose description correctly says the operation
    # is idempotent -- which is a true sentence about the tool, not an argument.
    # That is the seventh time in this codebase a text scan has made a claim
    # about structure it could not see (K10, K12, K18, K28, O30, M28): a module
    # is allowed to *name* a thing it does not do.
    for tool in _result(_call(_adapter(), "tools/list"))["tools"]:
        for schema_key in ("inputSchema", "outputSchema"):
            for prop in (tool[schema_key].get("properties") or {}):
                assert "idempot" not in prop.lower(), \
                    "%s.%s declares %r" % (tool["name"], schema_key, prop)
        submission = (tool["inputSchema"].get("properties") or {}).get("submission")
        if submission:
            assert sorted(submission["properties"]) == \
                sorted(O.SUBMISSION_FIELDS), \
                "the submission document gained a transport field"

    source = inspect.getsource(M)
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "idempotency_key":
                assert isinstance(keyword.value, ast.Constant) \
                    and keyword.value.value is None, \
                    "the MCP transport must pass a literal None, not a value"
                found.append(node)
    assert len(found) == 1, \
        "expected exactly one call into the ORS adapter's tool, found %d" % len(found)

    # And no cache: the one dict this module keeps holds ids, never results.
    adapter = _adapter()
    scored = _episode(adapter)
    assert list(adapter._spent.values()) == [scored["episode_id"]]
    assert all(isinstance(v, str) for v in adapter._spent.values()), \
        "_spent must hold episode ids, never results -- a result would be a cache"


def test_m21_the_refusal_is_per_handle_so_a_new_session_scores_again():
    """One handle, one episode. A new handle is a new episode, and says so.

    The complement of M19. A transport that refused a second submission by
    remembering *the task* rather than *the handle* would have turned a one-shot
    session into a one-shot server, which is a much larger claim than the
    substrate makes.
    """
    adapter = _adapter()
    first = _result(_submit(adapter, _open(adapter)))["structuredContent"]
    second = _result(_submit(adapter, _open(adapter)))["structuredContent"]

    assert first["session_id"] != second["session_id"]
    assert first["reward"] == second["reward"] == 1.0
    # The candidate is identical, so the episode is content-identical too. That
    # is content addressing working, not a cache: the bundle was re-verified and
    # re-published on the second pass.
    assert first["episode_id"] == second["episode_id"]

    # Closing a handle releases it, and closing twice is not an error.
    closed = _result(_tool(adapter, "close_session",
                           {"session_id": first["session_id"]}))
    assert closed["structuredContent"]["closed"] is True
    assert _result(_tool(adapter, "close_session",
                         {"session_id": first["session_id"]})
                   )["structuredContent"]["closed"] is True


# ================================= M22-M23: two error channels, one dividing line
def test_m22_refusals_land_on_the_channel_that_matches_who_can_fix_them():
    """`isError` for what the model can act on; JSON-RPC for what it cannot.

    MCP's two channels mean different things, and the question that sorts them
    is the one ORS answered with HTTP status codes: **is this the caller's to
    fix?** The spec is explicit that clients SHOULD feed tool execution errors
    back to the model for self-correction and that protocol errors are "less
    likely to result in successful recovery".

    So an unknown handle, a spent session, a malformed submission and a substrate
    with no `step` are all `isError: true` -- each names something a model can do
    differently. A version mismatch, a missing `_meta` field, an unknown method
    and an unknown tool are protocol errors, because no argument the model could
    change would help.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    tool_errors = [
        _tool(adapter, "open_session", {"task_id": "task-nope"}),
        _tool(adapter, "submit_candidate",
              {"session_id": "session-nope", "submission": _submission()}),
        _tool(adapter, "submit_candidate",
              {"session_id": session_id, "submission": {"bad": 1}}),
        _tool(adapter, "step"),
    ]
    for response in tool_errors:
        result = _result(response)
        assert result["isError"] is True, response
        assert result["structuredContent"]["error"]["code"], response
        assert result["content"][0]["type"] == "text", \
            "a model reads content blocks; a refusal must have one"

    protocol_errors = [
        _call(adapter, "tools/list", meta=_meta(version="1999-01-01")),
        _call(adapter, "nope/nope"),
        _tool(adapter, "frobnicate"),
        _call(adapter, "resources/read", {"uri": "trvs://task/task-nope"}),
    ]
    for response in protocol_errors:
        assert "error" in response, response
        assert isinstance(response["error"]["code"], int)

    # `tools/call` with a non-object `arguments` fails the CallToolRequest schema
    # itself, which the spec places on the protocol side -- as distinct from
    # failing the *tool's* inputSchema, which is a tool execution error.
    assert _error(_call(adapter, "tools/call",
                        {"name": "list_tasks", "arguments": "nope"})
                  )["code"] == M.ERR_INVALID_PARAMS


def test_m23_a_server_defect_is_never_offered_to_a_model_as_self_correctable():
    """`ORS_PUBLISH_FAILED` and `KERNEL_RUN_RESULT_MISSING` are `-32603`.

    These say *this server is broken*. Handing "the evidence could not be
    published" to a language model as something to correct invites it to retry
    forever, and each retry re-runs a verifier plan.

    `KERNEL_RUN_RESULT_MISSING` deserves its own sentence. A client cannot cause
    it -- this adapter builds the run result, never the client -- so if it ever
    reaches a client it is a defect here, and `-32603` is the only honest code.
    Reporting it as `isError` would invite a model to fix an input it does not
    have.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    real = EB.write_episode_bundle

    def boom(*args, **kwargs):
        raise EB.EpisodeBundleError("disk went away")

    EB.write_episode_bundle = boom
    try:
        error = _error(_submit(adapter, session_id))
    finally:
        EB.write_episode_bundle = real
    assert error["code"] == M.ERR_INTERNAL
    assert error["data"]["code"] == "ORS_PUBLISH_FAILED"

    # The mapping is stated as data, and every entry is a 5xx-equivalent.
    assert set(M._PROTOCOL_ERRORS) == {
        "KERNEL_RUN_RESULT_MISSING", "KERNEL_RUN_RESULT_UNEXPECTED",
        "ORS_PUBLISH_FAILED", "ORS_NO_EVIDENCE"}
    assert set(M._PROTOCOL_ERRORS.values()) == {M.ERR_INTERNAL}

    for code in M._PROTOCOL_ERRORS:
        raised = None
        try:
            adapter._tool_error(K.KernelError(code, "synthetic", {}))
        except M.McpError as exc:
            raised = exc
        assert raised is not None, "%s must not become a tool error" % code
        assert raised.code == M.ERR_INTERNAL
        assert raised.data["code"] == code


# ============================== M24-M27: the linearization survives the wire
def test_m24_two_different_sessions_score_at_the_same_time_over_the_wire():
    """A barrier inside the scoring work, driven through real pipes.

    The obvious stdio server is read-handle-write, and it is wrong for this
    kernel in a way that is invisible until it matters: the kernel stays
    linearizable and the *server* scores one episode at a time, so K25's property
    is preserved on paper and lost in fact.

    This law would **deadlock** such a server rather than merely run slowly,
    which is what makes it a proof. Both sessions must be inside
    `_finish_episode` simultaneously for the barrier to clear.
    """
    adapter = _adapter()
    first, second = _open(adapter), _open(adapter)
    assert first != second

    with _Live(adapter) as live, _Barrier(2) as barrier:
        ids = []
        for session_id in (first, second):
            _IDS[0] += 1
            ids.append(_IDS[0])
            live.send(_request("tools/call",
                               {"name": "submit_candidate",
                                "arguments": {"session_id": session_id,
                                              "submission": _submission()}},
                               ident=ids[-1]))
        results = [_result(live.await_id(i))["structuredContent"] for i in ids]

    assert not barrier.broken, "the two sessions did not overlap: %s" % barrier.broken
    assert all(r["finished"] for r in results)
    assert {r["session_id"] for r in results} == {first, second}


def test_m25_two_submissions_to_one_handle_score_exactly_once():
    """The kernel's claim, seen through the wire. One scores; the other is refused.

    The counter is the point, and it is why `_HeldScoring` counts entries rather
    than successes. "Exactly one submission succeeded" is strictly weaker than
    "the work ran once" -- the defect the Finalize Linearization Closure fixed
    satisfied the first (both callers got the same receipt) while violating the
    second, which for a Residency task means executing a candidate's test suite
    twice.

    The transport adds no lock of its own. It inherits the linearization, and
    this law is what makes "inherits" checkable.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    with _Live(adapter) as live, _HeldScoring() as held:
        ids = []
        for _ in range(2):
            _IDS[0] += 1
            ids.append(_IDS[0])
            live.send(_request("tools/call",
                               {"name": "submit_candidate",
                                "arguments": {"session_id": session_id,
                                              "submission": _submission()}},
                               ident=ids[-1]))
            if len(ids) == 1:
                assert held.entered.acquire(timeout=30), \
                    "the first submission never reached the scoring work"
        responses = []
        for ident in ids:
            responses.append(live.await_id(ident))
            held.release.set()

    results = [_result(r) for r in responses]
    scored = [r for r in results if not r["isError"]]
    refused = [r for r in results if r["isError"]]
    assert len(scored) == 1, "exactly one submission may score"
    assert len(refused) == 1
    assert refused[0]["structuredContent"]["error"]["code"] in (
        "KERNEL_SESSION_STATE", "ORS_SESSION_FINISHED"), refused[0]
    assert held.count == 1, \
        "the scoring work ran %d times; it must run once" % held.count


def test_m26_no_lock_is_held_across_scoring_at_either_adapter_layer():
    """Proven under load, not by reading source.

    The obvious way to "fix" a race is to hold a lock, which would make M25 pass
    and quietly reintroduce the one-episode-at-a-time server K15/K16 exist to
    prevent. So both adapters' locks are asserted **free** while an episode is
    suspended mid-scoring, and a whole second episode is driven to completion
    from the same thread.
    """
    ors_adapter, adapter, _kern = _pair()
    session_id = _open(adapter)

    with _HeldScoring() as held:
        thread = threading.Thread(
            target=lambda: _submit(adapter, session_id), daemon=True)
        thread.start()
        assert held.entered.acquire(timeout=30), "never reached scoring"

        for owner, lock in (("mcp", adapter._lock), ("ors", ors_adapter._lock)):
            assert lock.acquire(blocking=False), \
                "the %s adapter holds its lock across scoring" % owner
            lock.release()

        # And the server is genuinely still usable, not merely unlocked.
        assert _result(_call(adapter, "tools/list"))["tools"]
        held.release.set()
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert held.count == 1


def test_m27_the_framing_survives_concurrency_and_writes_are_serialized():
    """Every line on the wire is one complete message, and never a fragment.

    Framing is the one thing this transport genuinely owns: "messages are
    delimited by newlines and MUST NOT contain embedded newlines". Two responses
    written concurrently without a lock interleave into lines that are not JSON
    at all -- and a pipe write above `PIPE_BUF` is not atomic, so this is a real
    failure mode rather than a theoretical one.

    Checked twice: empirically, by firing many concurrent requests whose results
    are large; and structurally, because a load test can only ever fail to find
    the bug.
    """
    adapter = _adapter()
    with _Live(adapter) as live:
        ids = []
        for _ in range(12):
            _IDS[0] += 1
            ids.append(_IDS[0])
            live.send(_request("tools/list", ident=ids[-1]))
        for ident in ids:
            assert _result(live.await_id(ident))["tools"]
        raw = list(live.raw)

    assert len(raw) == 12, "expected one line per response, got %d" % len(raw)
    seen = set()
    for line in raw:
        assert line.endswith(b"\n") and b"\n" not in line[:-1], \
            "a framed message contained an embedded newline"
        message = json.loads(line.decode("utf-8"))   # each line stands alone
        seen.add(message["id"])
    assert seen == set(ids)

    # Structural: the write path takes a lock, and the encoder cannot emit a
    # literal newline (no `indent`), so the framing rule holds by construction.
    write = ast.parse(textwrap.dedent(inspect.getsource(MS.McpStdioServer._write)))
    withs = [n for n in ast.walk(write) if isinstance(n, ast.With)]
    assert withs, "_write must serialize its writes under a lock"
    assert "_write_lock" in _code_identifiers(inspect.getsource(MS.McpStdioServer))


# =============================== M28-M30: the transport, and what it did not add
def test_m28_stdio_has_no_network_surface_and_the_cli_refuses_to_pretend():
    """No socket, no bind, no port -- and flags from the other transport refused.

    ORS chose loopback with a blunt `--allow-remote`. Stdio is strictly better on
    that axis: the client is the process that launched this one and there is no
    port to expose, so `--allow-remote` has no meaning and deliberately does not
    exist.

    That is only true if it is *structurally* true, so the module is parsed
    rather than scanned -- its docstring explains at length that it has no socket
    and no port, and a text scan would read those very sentences as evidence that
    it does. This is the sixth time in this codebase a text scan would have made
    a claim about structure it could not see (K10, K12, K18, K28, O30).
    """
    names = _code_identifiers(inspect.getsource(MS))
    for forbidden in ("socket", "socketserver", "bind", "listen", "connect",
                      "http", "BaseHTTPRequestHandler", "ThreadingHTTPServer",
                      "urllib", "ssl"):
        assert forbidden not in names, \
            "mcp_server names %r; stdio has no network surface" % forbidden
    assert _code_identifiers(inspect.getsource(M)).isdisjoint(
        {"socket", "http", "urllib", "ssl"})

    from traaviis import cli
    parser = cli.build_parser()
    # Exactly one protocol, and it must be chosen.
    for argv in (["serve", "pkg", "--split", "dev", "--output", "out"],
                 ["serve", "pkg", "--ors", "--mcp", "--split", "dev",
                  "--output", "out"]):
        raised = False
        try:
            parser.parse_args(argv)
        except SystemExit:
            raised = True
        assert raised, "serve requires exactly one of --ors / --mcp: %s" % argv

    args = parser.parse_args(["serve", "pkg", "--mcp", "--split", "dev",
                              "--output", "out"])
    assert args.mcp and not args.ors
    assert not args.allow_remote

    # The ORS bind default stays **on the action**, where `--help` prints it and
    # where anyone asking "what does this bind to?" will look. `--mcp` still
    # tells "the operator typed --host" apart from "the operator did not" -- via
    # `explicit_options`, not by hollowing the default out to None. Adding a
    # second transport must not make the first one's ruled default unreadable.
    serve = parser._subparsers._group_actions[0].choices["serve"]
    defaults = {a.dest: a.default for a in serve._actions}
    assert defaults["host"] == "127.0.0.1", "the ORS bind default is loopback"
    assert defaults["port"] == 8080

    assert not getattr(args, "explicit_options", None)
    typed = parser.parse_args(["serve", "pkg", "--mcp", "--split", "dev",
                               "--output", "out", "--host", "0.0.0.0",
                               "--port", "9999"])
    assert typed.explicit_options == {"host", "port"}
    assert typed.host == "0.0.0.0", "the value is still stored, then refused"


def test_m29_the_environment_is_admitted_before_a_single_message_is_read():
    """`serve_stdio` admits, then serves. There is no other order.

    Over HTTP the argument was that a bound-but-unadmitted server teaches clients
    to retry. On stdio it is sharper: the client *launched* this process and will
    read "it started" as "it is serving". A package that does not admit must
    produce a failure before anything answers `tools/list`.

    Checked structurally -- `serve_stdio`'s body calls `open_adapter` before it
    constructs a server -- and behaviourally, against a package that cannot
    admit.
    """
    body = ast.parse(textwrap.dedent(inspect.getsource(MS.serve_stdio))).body[0]
    calls = [n.func.attr for n in ast.walk(body)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "open_adapter" in calls
    constructed = [n for n in ast.walk(body)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "McpStdioServer"]
    assert len(constructed) == 1
    assert calls.index("open_adapter") == 0, \
        "admission must be the first thing serve_stdio does"

    missing = os.path.join(_tmp(), "not-a-package")
    raised = None
    try:
        MS.serve_stdio(missing, "dev", _out("admit-fail"))
    except (AdmissionError, OSError) as exc:
        raised = exc
    assert raised is not None, "an unopenable package must not produce a server"

    # `mcp.open_adapter` has no admission logic of its own: it delegates to the
    # one ORS path. A second admission path would be a second server.
    source = inspect.getsource(M.open_adapter)
    assert "open_adapter" in _code_identifiers(source)
    assert "environment_kernel" not in _code_identifiers(source)


def test_m30_the_transport_added_no_rung_no_verb_and_no_second_pipeline():
    """A completeness check: this slice is a translation layer, and no more.

    The things a second transport could quietly become: a second identity
    authority, a second scoring pipeline, a second admission path, a second CLI
    surface. Each is checked structurally, because each would *work* -- it would
    simply produce a second set of answers that could drift from the first.
    """
    numbers = sorted(int(n.split("_")[1][1:]) for n in _law_names())
    assert numbers == list(range(1, 32)), numbers
    assert len(_law_names()) == 31

    # No new rung. `identity.py` does not know this transport exists, checked by
    # whole word so that a syllable inside `separators` cannot fail it.
    import re
    ident_words = set()
    for token in re.findall(r"[A-Za-z_]+", inspect.getsource(I)):
        ident_words.update(p for p in token.lower().split("_") if p)
    for word in ("mcp", "jsonrpc", "rpc", "stdio", "prompt", "resource"):
        assert word not in ident_words, "identity.py mentions %r" % word

    # No new CLI verb: `--mcp` is a flag on the existing `serve`.
    from traaviis import cli
    verbs = set(cli.build_parser()._subparsers._group_actions[0].choices)
    assert "serve" in verbs
    assert "serve-mcp" not in verbs and "mcp" not in verbs and "repl" not in verbs

    # The CLI drives an adapter; the adapter drives the substrate. A command that
    # constructed one itself would make the CLI a third transport.
    cli_src = inspect.getsource(cli)
    assert "EpisodeKernelV1" not in cli_src
    assert "kernel" not in cli_src.lower()

    # No second pipeline. This module computes no reward, builds no receipt,
    # writes no bundle, derives no identity, and constructs no RunResult.
    mcp_names = _code_identifiers(inspect.getsource(M))
    for forbidden in ("identity", "reward", "RunResult", "build_receipt_v1",
                      "write_episode_bundle", "episode_bundle", "evalone",
                      "runner", "trusted_run_result", "submission_trace",
                      "environment_kernel", "finalize", "KernelError"):
        assert forbidden not in mcp_names, \
            "mcp.py names %r; it must translate, not re-implement" % forbidden
    assert "RunResult" not in _code_identifiers(inspect.getsource(MS))

    # The refusal vocabulary this slice minted, read from the constructions
    # themselves and asserted literal.
    assert _refusal_codes(inspect.getsource(M), "McpRefusal") == [
        "MCP_ARGUMENT_MISSING", "MCP_ARGUMENT_UNKNOWN"]
    assert _refusal_codes(inspect.getsource(M), "OrsError") == [], \
        "the ORS refusal vocabulary is ors.py's; this module relays it"
    assert _refusal_codes(inspect.getsource(M), "KernelError") == [], \
        "the adapter must relay kernel refusals, never manufacture them"

    # The ORS transport is untouched by this slice.
    from traaviis import ors_server as OS
    assert "mcp" not in _code_identifiers(inspect.getsource(OS))
    assert "mcp" not in _code_identifiers(inspect.getsource(O))


def test_m31_a_cancelled_request_loses_its_report_but_not_its_result():
    """Cancellation is honoured exactly as far as it can honestly go.

    The spec requires that a server send no further messages for a cancelled
    request, and says work SHOULD stop "as soon as practical". This server
    **suppresses the response and does not stop the work**, because it cannot:
    the kernel has no cancel, `finalize` is a one-shot claim, and interrupting a
    verifier plan mid-flight would leave the session `finalizing` forever --
    un-closeable by `KERNEL_SESSION_BUSY` and un-scorable by anyone. Killing the
    work to look responsive would trade a slow answer for a permanently wedged
    session.

    So the honest statement is the one this law pins: the *report* is lost and
    the *result* is not. The episode is still published, and the next submission
    on that handle is refused with a link to it -- which is the same recovery
    M19 provides, arrived at from a different direction.
    """
    adapter = _adapter()
    session_id = _open(adapter)

    with _Live(adapter) as live, _HeldScoring() as held:
        _IDS[0] += 1
        doomed = _IDS[0]
        live.send(_request("tools/call",
                           {"name": "submit_candidate",
                            "arguments": {"session_id": session_id,
                                          "submission": _submission()}},
                           ident=doomed))
        assert held.entered.acquire(timeout=30), "never reached scoring"
        live.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": doomed}})
        held.release.set()

        # A later request proves the server is still healthy and orders the
        # stream: if the cancelled response were coming, it would be here by now.
        assert _result(live.request("tools/list"))["tools"]
        assert not live.has_id(doomed), \
            "a cancelled request must produce no further message"

    assert held.count == 1, "the scoring work still ran, exactly once"

    # And the result survived: the handle is spent, and says where its evidence
    # went. A cancelled submission loses its report, not its episode.
    refused = _result(_submit(adapter, session_id))
    assert refused["isError"] is True
    detail = refused["structuredContent"]["error"]["detail"]
    assert detail["episode_id"].startswith("episode-")
    receipt = json.loads(_result(_call(adapter, "resources/read",
                                       {"uri": detail["episode_uri"]}
                                       ))["contents"][0]["text"])
    assert receipt["episode_id"] == detail["episode_id"]
    assert receipt["reward"] == 1.0


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = skipped = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print("PASS %s" % t.__name__)
        except Skip as s:
            skipped += 1
            print("SKIP %s (%s)" % (t.__name__, s))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
    print("\n%d passed, %d skipped, %d failed" % (passed, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
