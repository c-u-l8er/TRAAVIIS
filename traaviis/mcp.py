"""`Residency MCP Profile v1` — the Episode Kernel behind the MCP vocabulary.

Built against **MCP specification revision `2026-07-28`**. That revision number is
not decoration: `2026-07-28` is the release that made MCP a *stateless* protocol.
It removed the `initialize` / `notifications/initialized` handshake, removed
protocol-level sessions and the `Mcp-Session-Id` header, and replaced them with
per-request `_meta` and this instruction to servers that need state:

    "Servers that need cross-call state use explicit, server-minted handles
     passed as ordinary tool arguments."

A transport that does not name the revision it speaks is making an unfalsifiable
claim, so this module names it in `MCP_PROTOCOL_VERSION` and refuses any other
version by name.

Why this is an adapter and not a second kernel
----------------------------------------------

The seam that matters is not "which module do I import". It is: *does this module
compute a reward, open an episode, mint an identity, or decide when a session may
be scored?* This one does none of those. It has no reward arithmetic, no receipt
builder, no `write_episode_bundle` call, no identity derivation, and — the load
bearing one — **no lock of its own around scoring**.

It composes `ors.OrsAdapterV1`, which is misnamed by history: read its docstring
and it describes itself as *transport-independent*, with `ors_server` as "a thin
encoding of it". `ors.py` is the Residency **submission semantics** — session
lifecycle, the one tool, the trust boundary, the publish-then-claim-finished
rule. `ors_server.py` is one encoding of that over HTTP. This module is a second
encoding of the same thing over JSON-RPC. Duplicating `trusted_run_result` here
so that `mcp.py` could touch the kernel directly would have created **two**
places that decide what the server is willing to believe a client did, which is
the precise failure the kernel extraction was performed to prevent.

Tools, resources, prompts — the mapping, and the argument for it
----------------------------------------------------------------

The three MCP vocabularies are not three ways to say the same thing; they differ
in *who drives* and in *what happens when you do it twice*.

**Resources are the content-addressed and immutable.** Reading one twice returns
the same bytes, and reading one has no effect. That is exactly the property of an
`env-…`, a `task-…` and a published `episode-…`: their names are derived from
their content, so "is my copy stale?" is a question that cannot have a
interesting answer. They get a `trvs://` URI scheme and a long `ttlMs`, and the
long ttl is *earned by content addressing* rather than guessed.

**Tools are the things that change something or consume something.** A session is
one-shot-scorable: `submit_candidate` spends a chance that does not come back.
That is the opposite of a resource read in every respect that matters, so it is a
tool and could not have been anything else.

**A session is neither.** It is mutable, ephemeral, and derives from randomness
rather than content (`kernel.SESSION_PREFIX` exists to make that visible at a
glance). Publishing `trvs://session/<id>` would put a thing with none of a
resource's properties into the namespace whose whole meaning is those properties.
So sessions appear only as tool arguments and tool results — which is precisely
what the `2026-07-28` handle guidance asks for, arrived at from the other end.

**Prompts are user-driven templates.** There is one, `residency_task`, taking a
`task_id`. The alternative — one prompt per task — was rejected because the
*template* does not vary; only its data does, and data that varies belongs in an
argument. N copies of one template is a catalog that grows without adding
meaning, and `prompts/list` would then change shape with the package.

Few coarse tools, not many fine ones
------------------------------------

The ecosystem convention (Graphonomous's five loop-phase machines) is coarse
tools. Here the question answers itself for a different reason: the substrate has
**one** operation with any content in it. `submit_candidate` is the episode;
`open_session` and `close_session` are the handle's constructor and destructor,
which the stateless protocol requires to be explicit; `list_tasks` is a read.
There is no design freedom to spend — a second scoring tool would have to mean
something, and Residency has nothing for it to mean.

The three operations this substrate refuses — `observe`, `step`, `reset` — are
**not** in `tools/list`, and are **still routable** by `tools/call`. Both halves
are deliberate:

* Not listed, because `tools/list` is fed to a language model as a menu. ORS
  §2's rule — "an advertised capability is a *claim*" — is strictly sharper here
  than it was over HTTP: a client's retry loop discovers a bad claim at 3am, but
  a model discovers it immediately and repeatedly, because the menu told it the
  operation exists.
* Still routable, because if they were simply absent, calling one would produce
  "unknown tool", which is a claim about *this server's catalog*. The truth is a
  claim about the *substrate*, and the client deserves to be told which. So a
  name in `kernel.OPERATIONS` gets the kernel's own
  `KERNEL_OPERATION_UNSUPPORTED`, relayed verbatim; a name in neither the catalog
  nor that frozen vocabulary gets `-32602`.

Idempotency: refused, not replayed
-----------------------------------

ORS put an `Idempotency-Key` in an HTTP *header* because retry safety is a
property of the wire. MCP's JSON-RPC `id` is a correlation token, not an
idempotency key, and this module deliberately adds **no** key of its own.

The reason is that the two wires have genuinely different retry semantics. HTTP
retries happen *below the caller*: a proxy, a reset connection or a client
library can re-send a POST with the caller unaware, so a key is needed to stop a
machine-generated duplicate from becoming a second episode. Under `2026-07-28`
the opposite is specified — stream resumability and message redelivery were
**removed**, and a client whose response stream breaks "MUST re-issue it as a new
request with a new request ID". Retries are explicit, caller-driven, and already
distinguishable.

Adding an `idempotency_key` tool *argument* would have been worse than nothing.
Tool arguments are written by a language model. A key a model invents is not an
idempotency key; it is a token that makes double-execution *look* deduplicated.
And by ORS §3's own logic an argument is client-supplied content that steers
server behaviour — the category `RemoteSubmissionV1` exists to keep empty.

So the honest answer is the one the kernel already gives: exactly one caller
leaves `_claim_finalize` holding the claim, and everyone else is **refused by
name**. Concretely, passing `idempotency_key=None` through to the ORS adapter
makes ORS's own O21 rule ("a keyless second submission is refused") the MCP rule,
with no new mechanism and no cache.

A refusal that is not a dead end
--------------------------------

Refusing rather than replaying costs one thing: a client that lost the response
cannot get the reward back. That is repaired without reintroducing a cache. The
`ORS_SESSION_FINISHED` refusal is enriched with the `episode_id` this handle
already produced and a `trvs://episode/…` link, so the answer is *navigable*
rather than *replayed*. It is still a refusal — `isError: true`, no reward field,
the session stays spent — it simply points at the evidence, which is on disk
because `finished: true` was only ever returned after it got there.

Errors: the same line ORS drew, drawn again
--------------------------------------------

MCP has two error channels and they mean different things. The question that
sorts them is the one ORS answered with HTTP status codes: **is this the
caller's to fix?**

* `isError: true` — the model can act on it. Malformed submissions, unknown
  handles, a spent session, a substrate that has no `step`. The spec is explicit
  that clients SHOULD hand these back to the model for self-correction.
* JSON-RPC protocol error — the model cannot act on it. Version mismatches,
  missing required `_meta`, unknown methods, unknown tool names, and every
  refusal that means *this server is broken* (`ORS_PUBLISH_FAILED`,
  `KERNEL_RUN_RESULT_MISSING`).

`KERNEL_RUN_RESULT_MISSING` is worth stating separately. A client cannot cause
it — the adapter builds the `RunResult`, never the client — so if it ever reaches
a client it is a defect in this server, and `-32603` is the only honest code.
Reporting it as `isError: true` would invite a model to "correct" an input it
does not have.

Pagination: not implemented, therefore refused
-----------------------------------------------

`2026-07-28` gives four operations an optional `cursor` — `tools/list`,
`resources/list`, `resources/templates/list`, `prompts/list` — and an optional
`nextCursor` in the reply. This server implements neither half. The catalog is
one environment, a handful of tasks and one prompt, fixed at admission; a page
boundary would be a boundary invented for its own sake.

What is *not* acceptable is the shape that omission had first. A cursor arrived,
was never read, and the client received page one of a single-page result with no
field distinguishing that from the page it asked for. A client cannot tell
"your cursor was honoured and this is the last page" from "your cursor was
discarded" — the two produce byte-identical responses. That is the failure this
repo exists to refuse: a divergence that is silent rather than refusable.

So a **supplied** cursor is `-32602`, which is the code the pagination spec
itself names for one ("Invalid cursors SHOULD result in an error with code
-32602"). The claim is stronger than "invalid": the only way a client can come by
a cursor for this server is a `nextCursor` this server never emits, so **every**
cursor it could be sent is one the client invented. Refusing the whole class is
therefore not a restriction on what a well-behaved client can do — a
well-behaved client has nothing to send — it is a statement of where the
capability stops.

`cursor: null` is treated as **absent** and answered normally. The spec's own
only-permitted determination on a cursor value is null-versus-not ("Don't make
any determination based on cursor value other than whether a non-null value was
provided"), and it turns on that distinction to rule that `""` is a real cursor.
A JSON `null` in an optional field is what a typed client emits for "I have no
cursor"; refusing it would refuse a request for page one on the grounds that it
said so explicitly. The empty string is refused with everything else, because the
spec says in as many words that it is a cursor.

`PAGINATED_METHODS` is enumerated from the specification's own list of operations
supporting pagination, not from this module's dispatch table, and the check runs
once for all of them rather than per handler. Enumerating from the code would
make the boundary describe the implementation instead of the protocol, and would
go quietly wrong the moment a list method was added without one.
"""

import json
import os
import threading

from . import boundedjson as _bjson
from . import kernel as _kernel
from . import ors as _ors
from . import substrates as _substrates

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SUPPORTED_VERSIONS",
    "MCP_PROFILE_VERSION",
    "SERVER_INFO",
    "URI_SCHEME",
    "META_PROTOCOL_VERSION",
    "META_CLIENT_CAPABILITIES",
    "META_CLIENT_INFO",
    "META_SERVER_INFO",
    "TOOLS",
    "PROMPT_NAME",
    "PAGINATED_METHODS",
    "PAGINATION_PARAM",
    "SUPPORTS_PAGINATION",
    "McpError",
    "McpRefusal",
    "McpAdapterV1",
    "open_adapter",
]

#: The MCP specification revision this module implements, and the only one it
#: accepts. Any other value in a request's `_meta` is refused with
#: `UnsupportedProtocolVersionError` (-32022) listing what is supported —
#: which is the spec's own mechanism for a client to fall back correctly.
MCP_PROTOCOL_VERSION = "2026-07-28"

#: Every revision this server can speak. One. A server that listed a revision it
#: had not implemented would be making the same kind of false claim as a tool
#: catalog that lists an operation the substrate refuses.
MCP_SUPPORTED_VERSIONS = (MCP_PROTOCOL_VERSION,)

#: This adapter's own profile name, distinct from the MCP revision. One says
#: which protocol is spoken; the other says what is being said in it.
MCP_PROFILE_VERSION = "traaviis.mcp-profile.v1"

SERVER_INFO = {"name": "traaviis", "version": "0.1.0"}

#: The URI scheme for content-addressed TRAAVIIS artifacts. Every authority under
#: it names a rung of the identity ladder, so a URI is a *derivation*, not a
#: location — which is what makes the long `ttlMs` below honest.
URI_SCHEME = "trvs"

# --- the per-request `_meta` keys the stateless core requires -----------------
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# --- JSON-RPC / MCP error codes ----------------------------------------------
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
#: `2026-07-28` allocated -32020..-32099 to the specification and renumbered the
#: codes introduced in draft. -32022 is `UnsupportedProtocolVersion`.
ERR_UNSUPPORTED_PROTOCOL_VERSION = -32022

#: Freshness hints. One hour for catalogs (the catalog is fixed at admission —
#: `open_adapter` restricts it to one split and nothing may add to it while the
#: process lives) and one hour for episode reads (content-addressed, therefore
#: immutable, therefore incapable of going stale at all).
TTL_CATALOG_MS = 3600000
TTL_EPISODE_MS = 3600000

#: `cacheScope` says whether a shared intermediary may keep a copy. Task and
#: environment documents are the published benchmark and are `public`. An
#: episode contains a submitter's finding and patch, so it is `private` — the
#: distinction is about whose content it is, not about how it is addressed.
SCOPE_PUBLIC = "public"
SCOPE_PRIVATE = "private"

TOOL_LIST_TASKS = "list_tasks"
TOOL_OPEN_SESSION = "open_session"
TOOL_SUBMIT = "submit_candidate"
TOOL_CLOSE_SESSION = "close_session"

#: The whole catalog, in a fixed order. `2026-07-28` asks for a deterministic
#: order so clients can cache and so an LLM's prompt cache hits; a tuple is the
#: cheapest way to make "deterministic" structural rather than incidental.
TOOLS = (TOOL_LIST_TASKS, TOOL_OPEN_SESSION, TOOL_SUBMIT, TOOL_CLOSE_SESSION)

PROMPT_NAME = "residency_task"

#: The operations `2026-07-28` gives a `cursor`, transcribed from the
#: specification's "Operations Supporting Pagination" list and in its order. This
#: is deliberately **not** derived from `_dispatch`: the set is a property of the
#: protocol, and one read off this module's own routing would describe what this
#: server happens to implement while claiming to describe MCP. A method here that
#: this server does not serve is harmless (it is unreachable); a method this
#: server serves that is missing from here is the defect, and a law checks for it
#: by parsing the dispatch table rather than by trusting this tuple.
PAGINATED_METHODS = ("resources/list", "resources/templates/list",
                     "prompts/list", "tools/list")

#: Whether this revision of this server pages. It does not — and because a cursor
#: can only be obtained from a `nextCursor` that is never emitted, "does not page"
#: and "accepts no cursor" are the same statement. Declared on `server/discover`
#: under this project's own prefix so the boundary is *readable* rather than only
#: discoverable by tripping over it.
SUPPORTS_PAGINATION = False

#: The parameter name that carries a page position. One place, so the refusal and
#: the law that drives it cannot disagree about which key is being checked: M38
#: builds every request it sends through this name rather than through a literal
#: of its own, which is what makes "one place" true rather than merely asserted.
#:
#: The value is still pinned to the specification by exactly *one* literal, in
#: M38. That literal is not a duplicate of this line, it is the other half of the
#: claim: without it, renaming this constant would rename the key this server
#: checks and every law would stay green while the server stopped implementing
#: the spec's pagination. With it, a rename is a failure — and a *relocation* of
#: the key is still one edit, which is the property the constant is for.
PAGINATION_PARAM = "cursor"

#: Typed refusals that mean **this server is broken**, and the JSON-RPC code each
#: becomes. Everything not listed here is the caller's to fix and becomes a tool
#: execution error (`isError: true`).
#:
#: Stated as data rather than derived from the code's prefix, for the same reason
#: `ors_server._STATUS` is: prefix is not the question. `ORS_SESSION_FINISHED`
#: and `ORS_PUBLISH_FAILED` share a prefix and are opposite answers to "can the
#: caller do anything about this?".
_PROTOCOL_ERRORS = {
    # The adapter builds the RunResult; a client cannot supply or withhold one.
    # Reaching these from the wire means this server has a defect.
    "KERNEL_RUN_RESULT_MISSING": ERR_INTERNAL,
    "KERNEL_RUN_RESULT_UNEXPECTED": ERR_INTERNAL,
    # The episode scored and its evidence could not be persisted. No argument
    # the model could change would alter that.
    "ORS_PUBLISH_FAILED": ERR_INTERNAL,
    "ORS_NO_EVIDENCE": ERR_INTERNAL,
}


class McpRefusal(_substrates.AdmissionError):
    """A typed refusal this adapter mints itself: bad *tool arguments*.

    Distinct from `OrsError` on purpose. `ors.py` owns the vocabulary for a bad
    *submission* — the document, its exact key set, its forbidden fields — and
    this owns the vocabulary for a bad *call*: an argument that is missing, or an
    argument nobody asked for. Reusing `OrsError` here would have grown the ORS
    refusal vocabulary with names that mean nothing to an ORS client, and the law
    that pins that vocabulary would have had to be loosened to let them in.
    """


class McpError(Exception):
    """A JSON-RPC **protocol** error: an integer code, a message, optional data.

    Deliberately a different type from `substrates.AdmissionError`. The typed
    refusals in this codebase describe a *substrate* judgement and carry string
    codes; this describes a *protocol* fault and carries an integer one. Making
    them one type would have meant one `except` clause deciding a question this
    module answers with a table.
    """

    def __init__(self, code, message, data=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


def _text(value):
    return {"type": "text", "text": value}


def _json_text(obj):
    return _text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def episode_uri(episode_id):
    """The `trvs://` URI for a published episode."""
    return "%s://episode/%s" % (URI_SCHEME, episode_id)


def task_uri(task_id):
    return "%s://task/%s" % (URI_SCHEME, task_id)


def env_uri(env_id):
    return "%s://env/%s" % (URI_SCHEME, env_id)


class McpAdapterV1(object):
    """The MCP vocabulary over one `ors.OrsAdapterV1`.

    Pure and socket-free: `handle` takes a decoded JSON-RPC message and returns a
    decoded response (or `None` for a notification). That is what lets the
    M-battery drive the entire protocol — version negotiation, refusal mapping,
    concurrency — without a pipe or a subprocess, exactly as the O-battery drives
    ORS without a socket. `mcp_server` adds framing and threads and no decisions.
    """

    def __init__(self, adapter, *, output=None):
        self._adapter = adapter
        self._output = output
        #: handle → the `episode-…` that handle already produced. **Not** an
        #: idempotency cache: nothing is ever replayed out of it, and it holds no
        #: result. It exists so the `ORS_SESSION_FINISHED` refusal can point at
        #: evidence instead of being a dead end. A cache would return the reward
        #: again; this returns a refusal that knows where the reward went.
        self._spent = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- discovery
    def describe(self):
        """The `DiscoverResult` body. `server/discover` is a MUST in this revision.

        Capabilities are declared **empty** — `{"tools": {}, ...}` — with no
        `listChanged` and no `subscribe`. The catalog is frozen by
        `ors.open_adapter` before anything is served and nothing can add to it
        while the process lives, so there is no notification this server could
        ever honestly emit. Declaring `listChanged: true` and never emitting is
        the same species of false claim as listing a tool that always refuses.
        """
        desc = self._adapter.describe()
        return {
            "supportedVersions": list(MCP_SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "instructions": (
                "TRAAVIIS serves one packed, content-addressed benchmark "
                "environment (%s) over the %s substrate. The substrate is "
                "one-shot: open_session mints a handle, submit_candidate spends "
                "it exactly once and returns a verifiable reward, close_session "
                "releases it. There is no observation between steps and no "
                "retry — a spent handle is refused, not re-scored. Every episode "
                "is published to disk as a content-addressed bundle, replayable "
                "offline with `trvs verify-episode`; %s://episode/<episode_id> "
                "serves that bundle's sealed receipt, which is one document "
                "inside it. This server does not paginate: no list method "
                "returns a nextCursor, and a cursor sent to one is refused."
                % (desc.get("env_id"), desc.get("substrate_profile"),
                   URI_SCHEME)),
            "ttlMs": TTL_CATALOG_MS,
            "cacheScope": SCOPE_PUBLIC,
            "_meta": {META_SERVER_INFO: dict(SERVER_INFO)},
            # Not part of the MCP schema; namespaced under this project's own
            # reverse-DNS prefix because the `_meta` rules reserve anything whose
            # second label is `modelcontextprotocol` or `mcp`. A client that does
            # not know TRAAVIIS ignores it; one that does can see which profile,
            # kernel and runner produced the receipts without calling a tool.
            "com.traaviis/profile": {
                "mcp_profile_version": MCP_PROFILE_VERSION,
                "ors_profile_version": desc.get("ors_profile_version"),
                "kernel_version": desc.get("kernel_version"),
                "substrate_profile": desc.get("substrate_profile"),
                "runner_profile": desc.get("runner_profile"),
                "submission_version": desc.get("submission_version"),
                "env_id": desc.get("env_id"),
                "operations": desc.get("operations", {}),
                # Stated, because a boundary a client can only find by tripping
                # over it is a boundary this server knows about and did not say.
                # Not an MCP capability — the schema has no flag for pagination,
                # and inventing one under the reserved prefix would be a claim
                # in somebody else's vocabulary.
                "supports_pagination": SUPPORTS_PAGINATION,
            },
        }

    # ----------------------------------------------------------------- tools
    def list_tools(self):
        """The tool catalog. Four tools, fixed order, no refusing operations.

        `observe` / `step` / `reset` are absent on purpose — see the module
        docstring. They remain reachable through `call_tool`, where they answer
        in the substrate's own words rather than as a missing menu entry.
        """
        submission_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": list(_ors.SUBMISSION_FIELDS),
            "properties": {
                "submission_version": {
                    "const": _ors.SUBMISSION_VERSION,
                    "description": "must be %r" % _ors.SUBMISSION_VERSION,
                },
                "finding": {
                    "type": ["object", "null"],
                    "description": "the candidate finding, or null",
                },
                "patch": {
                    "type": ["string", "null"],
                    "description": "a unified diff over the subject tree, or null",
                },
            },
        }
        return [
            {
                "name": TOOL_LIST_TASKS,
                "title": "List tasks",
                "description":
                    "List the task ids this environment serves. Read-only.",
                "inputSchema": {"type": "object", "additionalProperties": False},
                "outputSchema": {
                    "type": "object",
                    "required": ["tasks"],
                    "properties": {
                        "tasks": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {
                "name": TOOL_OPEN_SESSION,
                "title": "Open an episode session",
                "description":
                    "Admit a task and mint a session handle. The handle is "
                    "server-minted, opaque, and worth exactly one "
                    "submit_candidate call; it lives only as long as this "
                    "server process. Pass it back as session_id.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id"],
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "a task id from list_tasks",
                        },
                    },
                },
                "outputSchema": {
                    "type": "object",
                    "required": ["session_id", "task_id", "finished"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "finished": {"type": "boolean"},
                    },
                },
            },
            {
                "name": TOOL_SUBMIT,
                "title": "Submit a candidate",
                "description":
                    "Spend a session handle: submit one finding and patch, have "
                    "the server score them against the packed environment, and "
                    "receive the reward plus a link to the sealed receipt of "
                    "the published episode. "
                    "A handle may be submitted exactly ONCE — a second call is "
                    "refused, not re-scored. Do not send a reward, status, "
                    "verifier outcome, execution fact or identifier; those are "
                    "determined by the server and a submission carrying one is "
                    "refused.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["session_id", "submission"],
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "the handle from open_session",
                        },
                        "submission": submission_schema,
                    },
                },
                "outputSchema": {
                    "type": "object",
                    "required": ["session_id", "task_id", "episode_id",
                                 "status", "validity", "reward", "finished",
                                 "evidence_member"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "episode_id": {"type": "string"},
                        "status": {"type": "string"},
                        "validity": {"type": "string"},
                        "reward": {"type": ["number", "null"]},
                        "finished": {"type": "boolean"},
                        "evidence_member": {"type": "string"},
                        "episode_uri": {
                            "type": "string",
                            "description":
                                "reads back the episode's sealed receipt, not "
                                "its bundle",
                        },
                    },
                },
            },
            {
                "name": TOOL_CLOSE_SESSION,
                "title": "Close an episode session",
                "description":
                    "Release a session handle. Idempotent for a handle that "
                    "names nothing. Refused while another caller is scoring it.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["session_id"],
                    "properties": {"session_id": {"type": "string"}},
                },
                "outputSchema": {
                    "type": "object",
                    "required": ["session_id", "closed"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "closed": {"type": "boolean"},
                    },
                },
            },
        ]

    def call_tool(self, name, arguments):
        """Dispatch one `tools/call`. Returns a `CallToolResult` body.

        Raises `McpError` for a protocol fault; returns an `isError: true` result
        for anything the caller can act on. The sorting is done once, in
        `_tool_error`, against `_PROTOCOL_ERRORS`.
        """
        if not isinstance(arguments, dict):
            # A non-object `arguments` fails the CallToolRequest schema itself,
            # which the spec places on the protocol side — as opposed to failing
            # the *tool's* inputSchema, which is a tool execution error.
            raise McpError(ERR_INVALID_PARAMS,
                           "tools/call arguments must be an object, got %s"
                           % type(arguments).__name__)
        try:
            if name == TOOL_LIST_TASKS:
                return self._call_list_tasks(arguments)
            if name == TOOL_OPEN_SESSION:
                return self._call_open_session(arguments)
            if name == TOOL_SUBMIT:
                return self._call_submit(arguments)
            if name == TOOL_CLOSE_SESSION:
                return self._call_close_session(arguments)
        except _substrates.AdmissionError as exc:
            return self._tool_error(exc)

        if name in _kernel.OPERATIONS:
            # A frozen kernel operation this substrate does not implement. It is
            # not in the catalog (a menu must not advertise a refusal) but it is
            # not "unknown" either, and saying so would blame the transport for a
            # property of the substrate.
            try:
                self._adapter.unsupported(name)
            except _substrates.AdmissionError as exc:
                return self._tool_error(exc)
        raise McpError(
            ERR_INVALID_PARAMS, "Unknown tool: %s" % name,
            {"tool": name, "tools": list(TOOLS)})

    def _call_list_tasks(self, arguments):
        self._reject_extra(arguments, (), TOOL_LIST_TASKS)
        tasks = self._adapter.list_tasks()
        return self._ok([_json_text({"tasks": tasks})], {"tasks": tasks})

    def _call_open_session(self, arguments):
        self._reject_extra(arguments, ("task_id",), TOOL_OPEN_SESSION)
        task_id = self._require_str(arguments, "task_id", TOOL_OPEN_SESSION)
        session = self._adapter.open_session(task_id)
        out = {"session_id": session["session_id"],
               "task_id": session["task_id"],
               "finished": bool(session["finished"])}
        return self._ok(
            [_text("session %s opened for task %s — one submit_candidate call"
                   % (out["session_id"], out["task_id"])),
             _json_text(out)], out)

    def _call_close_session(self, arguments):
        self._reject_extra(arguments, ("session_id",), TOOL_CLOSE_SESSION)
        session_id = self._require_str(arguments, "session_id",
                                       TOOL_CLOSE_SESSION)
        closed = self._adapter.close_session(session_id)
        with self._lock:
            self._spent.pop(session_id, None)
        out = {"session_id": closed["session_id"],
               "closed": bool(closed["closed"])}
        return self._ok([_json_text(out)], out)

    def _call_submit(self, arguments):
        """The one tool with an episode in it.

        The submission is a **nested object**, not the argument map itself. That
        is the entire reason the trust boundary survives this second transport
        intact: `RemoteSubmissionV1` is validated by *exact key set*, so folding
        `session_id` in beside `finding` and `patch` would have forced either a
        fourth allowed key or a strip-then-validate step, and both turn "is this
        the document?" back into "does this contain the document?". The handle is
        transport framing and stays outside the document — the same argument that
        put ORS's idempotency key in a header rather than in the body.
        """
        self._reject_extra(arguments, ("session_id", "submission"), TOOL_SUBMIT)
        session_id = self._require_str(arguments, "session_id", TOOL_SUBMIT)
        submission = arguments.get("submission")

        try:
            output = self._adapter.call_tool(
                session_id, _ors.ORS_TOOL, submission, idempotency_key=None)
        except _substrates.AdmissionError as exc:
            if getattr(exc, "code", None) == "ORS_SESSION_FINISHED":
                return self._tool_error(exc, self._spent_detail(session_id))
            raise

        meta = output["metadata"]
        episode_id = meta["episode_id"]
        with self._lock:
            self._spent[session_id] = episode_id
        structured = {
            "session_id": session_id,
            "task_id": meta["task_id"],
            "episode_id": episode_id,
            "status": meta["status"],
            "validity": meta["validity"],
            "reward": output["reward"],
            "finished": bool(output["finished"]),
            "evidence_member": meta["evidence_member"],
            "episode_uri": episode_uri(episode_id),
        }
        blocks = [dict(block) for block in output["blocks"]]
        blocks.append(_json_text(structured))
        blocks.append({
            "type": "resource_link",
            "uri": episode_uri(episode_id),
            "name": episode_id,
            "description":
                "the sealed, content-addressed receipt for this episode: "
                "reward, status, validity, signals and verifier versions. "
                "Reading it returns the receipt document alone. The full "
                "bundle — task, reward spec, snapshot, trace and verifier "
                "evidence — is on the server's disk under this id, and it is "
                "the bundle, not this URI, that `trvs verify-episode` replays.",
            "mimeType": "application/json",
        })
        return self._ok(blocks, structured)

    def _spent_detail(self, session_id):
        """Where the evidence went, for a handle that has already been spent."""
        with self._lock:
            episode_id = self._spent.get(session_id)
        if not episode_id:
            return None
        return {"episode_id": episode_id, "episode_uri": episode_uri(episode_id)}

    # --- argument checking -------------------------------------------------
    def _reject_extra(self, arguments, allowed, tool):
        extra = sorted(set(arguments) - set(allowed))
        if extra:
            raise McpRefusal(
                "MCP_ARGUMENT_UNKNOWN",
                "%s takes %s; it was given %s"
                % (tool, ", ".join(allowed) or "no arguments", ", ".join(extra)),
                {"tool": tool, "unknown": extra, "allowed": list(allowed)})

    def _require_str(self, arguments, key, tool):
        value = arguments.get(key)
        if not isinstance(value, str) or not value:
            raise McpRefusal(
                "MCP_ARGUMENT_MISSING",
                "%s needs a non-empty string %s" % (tool, key),
                {"tool": tool, "argument": key})
        return value

    # --- results -----------------------------------------------------------
    def _ok(self, blocks, structured):
        return {"content": blocks, "structuredContent": structured,
                "isError": False}

    def _tool_error(self, exc, extra=None):
        """A typed refusal as a tool execution error the model can act on.

        Raises instead if `_PROTOCOL_ERRORS` says the refusal means this server
        is broken: handing "the disk is full" to a language model as something
        to self-correct is an invitation to retry forever.
        """
        code = getattr(exc, "code", None) or "MCP_ERROR"
        protocol = _PROTOCOL_ERRORS.get(code)
        detail = dict(getattr(exc, "detail", None) or {})
        message = getattr(exc, "message", None) or str(exc)
        if protocol is not None:
            raise McpError(protocol, message, dict(detail, code=code))
        if extra:
            detail.update(extra)
        body = {"error": {"code": code, "message": message, "detail": detail}}
        return {"content": [_text("%s: %s" % (code, message)), _json_text(body)],
                "structuredContent": body, "isError": True}

    # ------------------------------------------------------------- resources
    def list_resources(self):
        """The immutable closure: the environment, and every task in the split.

        Published episodes are **not** here. They are reachable through the
        `trvs://episode/{episode_id}` template and through the `resource_link`
        each `submit_candidate` returns — which is the pattern the spec names
        ("resource links returned by tools are not guaranteed to appear in the
        results of a `resources/list` request"). The reason to use it is that
        `resources/list` must not vary as a side effect of other requests, and
        episodes exist precisely because of other requests.
        """
        desc = self._adapter.describe()
        env_id = desc.get("env_id")
        out = [{
            "uri": env_uri(env_id),
            "name": env_id,
            "title": "Packed environment",
            "description":
                "the admitted environment: substrate, profiles, served tasks, "
                "and which kernel operations this substrate refuses",
            "mimeType": "application/json",
        }]
        for task_id in self._adapter.list_tasks():
            out.append({
                "uri": task_uri(task_id),
                "name": task_id,
                "title": "Task %s" % task_id,
                "description":
                    "the task document: objective, required signals, "
                    "termination, and the submission schema",
                "mimeType": "application/json",
            })
        return out

    def list_resource_templates(self):
        return [{
            "uriTemplate": "%s://episode/{episode_id}" % URI_SCHEME,
            "name": "episode",
            "title": "Published episode receipt",
            "description":
                "the sealed EpisodeReceiptV1 for a scored submission. "
                "Content-addressed, so the id is a derivation of the receipt "
                "and cannot name a different one later. The receipt only: the "
                "bundle it was sealed into — task, reward spec, snapshot, "
                "trace, verifier evidence — is not served over this scheme.",
            "mimeType": "application/json",
        }]

    def read_resource(self, uri):
        """Read one `trvs://` resource. Returns the `contents` list plus caching.

        An unknown URI is `-32602` — this revision moved resource-not-found off
        the old `-32002` to align with JSON-RPC — and the `contents` list is
        never returned empty for a missing resource, because an empty list cannot
        distinguish "exists and is empty" from "does not exist".
        """
        if not isinstance(uri, str):
            raise McpError(ERR_INVALID_PARAMS,
                           "resources/read needs a string uri")
        prefix = "%s://" % URI_SCHEME
        if not uri.startswith(prefix):
            raise self._no_resource(uri)
        rest = uri[len(prefix):]
        kind, _, ident = rest.partition("/")
        if not ident:
            raise self._no_resource(uri)

        desc = self._adapter.describe()
        if kind == "env":
            if ident != desc.get("env_id"):
                raise self._no_resource(uri)
            return self._contents(uri, desc, SCOPE_PUBLIC, TTL_CATALOG_MS)
        if kind == "task":
            try:
                body = self._adapter.task(ident)
            except _substrates.AdmissionError:
                # `KERNEL_TASK_UNKNOWN` is a real refusal, but on the resource
                # side the question asked was "does this URI name anything?" and
                # the answer the spec requires is `-32602`.
                raise self._no_resource(uri)
            return self._contents(uri, body, SCOPE_PUBLIC, TTL_CATALOG_MS)
        if kind == "episode":
            return self._contents(uri, self._read_episode(ident, uri),
                                  SCOPE_PRIVATE, TTL_EPISODE_MS)
        raise self._no_resource(uri)

    def _read_episode(self, episode_id, uri):
        """The sealed receipt of a published episode, read off disk.

        Only the receipt, and only by exact id. The bundle also holds the task,
        the reward spec, the snapshot, the trace and the verifier evidence —
        serving those through here would make this module a second, partial
        implementation of `verify-episode`'s reader, which has an opinion about
        closure that a resource read must not quietly approximate. A client that
        wants the closure reads the directory, or replays it.
        """
        if not self._output or not episode_id.startswith("episode-"):
            raise self._no_resource(uri)
        # `episode_id` is interpolated into a path, so it is checked for
        # separators and traversal before it touches the filesystem rather than
        # after. A content-addressed id never contains either.
        if os.sep in episode_id or "/" in episode_id or ".." in episode_id:
            raise self._no_resource(uri)
        path = os.path.join(self._output, episode_id, "receipt.json")
        if not os.path.isfile(path):
            raise self._no_resource(uri)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            receipt = _bjson.load_json_bounded(raw)
        except (OSError, _bjson.BoundedJsonError) as exc:
            # `OSError` and a bounds refusal share one code here, unlike in
            # `comparison._read_receipt`, because this is a *resource read* and
            # the client is told the same thing either way: the URI resolves to
            # something this server cannot serve. The clause caught
            # `(OSError, ValueError)`, so a deep receipt raised `RecursionError`
            # out of a resource read and killed the serve loop -- a published
            # episode being unreadable must degrade to a typed error, never to a
            # dead server, since the other episodes are still fine.
            raise McpError(ERR_INTERNAL,
                           "episode %s is published but unreadable: %s"
                           % (episode_id, exc))
        if receipt.get("episode_id") != episode_id:
            # The directory name and the sealed id disagree. Never paper over
            # this: the whole value of the resource space is that a `trvs://`
            # URI is a derivation of the bytes it returns.
            raise McpError(
                ERR_INTERNAL,
                "episode %s holds a receipt sealed as %r; refusing to serve a "
                "receipt under a name it does not claim"
                % (episode_id, receipt.get("episode_id")))
        return receipt

    def _no_resource(self, uri):
        return McpError(ERR_INVALID_PARAMS, "Resource not found", {"uri": uri})

    def _contents(self, uri, body, scope, ttl):
        return {
            "contents": [{
                "uri": uri,
                "name": uri.rsplit("/", 1)[-1],
                "mimeType": "application/json",
                "text": json.dumps(body, indent=2, sort_keys=True,
                                   ensure_ascii=False),
            }],
            "ttlMs": ttl,
            "cacheScope": scope,
        }

    # --------------------------------------------------------------- prompts
    def list_prompts(self):
        return [{
            "name": PROMPT_NAME,
            "title": "Residency task instructions",
            "description":
                "the full instruction text for one task: objective, required "
                "signals, termination, and the exact submission document",
            "arguments": [{
                "name": "task_id",
                "description": "a task id from list_tasks",
                "required": True,
            }],
        }]

    def get_prompt(self, name, arguments):
        """Render `residency_task` for one task.

        The text comes from `ors.OrsAdapterV1.task_prompt`, unmodified. Two
        agents given "the same task" must have been given the same task, and a
        second copy of the prompt text here would be a second thing that could
        drift from it — the prompt is already ruled to contain no mutable server
        state for exactly that reason.
        """
        if name != PROMPT_NAME:
            raise McpError(ERR_INVALID_PARAMS, "Unknown prompt: %s" % name,
                           {"prompt": name, "prompts": [PROMPT_NAME]})
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise McpError(ERR_INVALID_PARAMS,
                           "prompts/get arguments must be an object")
        task_id = arguments.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise McpError(ERR_INVALID_PARAMS,
                           "%s requires a task_id argument" % PROMPT_NAME,
                           {"prompt": PROMPT_NAME, "argument": "task_id"})
        try:
            text = self._adapter.task_prompt(task_id)
        except _substrates.AdmissionError as exc:
            raise McpError(ERR_INVALID_PARAMS,
                           getattr(exc, "message", None) or str(exc),
                           {"task_id": task_id})
        return {
            "description": "Residency task %s" % task_id,
            "messages": [{"role": "user", "content": _text(text)}],
        }

    # ------------------------------------------------------------- dispatch
    def handle(self, message):
        """Route one decoded JSON-RPC message. Returns a response, or None.

        None means "write nothing", which is correct for a notification and is
        the only correct answer for one: the spec forbids a response.
        """
        if not isinstance(message, dict):
            return _error_response(
                None, ERR_INVALID_REQUEST,
                "a JSON-RPC message must be an object")
        if message.get("jsonrpc") != "2.0":
            return _error_response(
                message.get("id"), ERR_INVALID_REQUEST,
                "jsonrpc must be \"2.0\"")
        method = message.get("method")
        if not isinstance(method, str):
            return _error_response(message.get("id"), ERR_INVALID_REQUEST,
                                   "a request needs a string method")
        ident = message.get("id")
        if ident is None:
            # A notification. Nothing this server exposes changes on one, and
            # `notifications/cancelled` is handled by the transport (which owns
            # the response it would have to suppress), not here.
            return None
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error_response(ident, ERR_INVALID_PARAMS,
                                   "params must be an object")
        try:
            self._check_meta(params)
            self._check_cursor(method, params)
            result = self._dispatch(method, params)
        except McpError as exc:
            return _error_response(ident, exc.code, exc.message, exc.data)
        except _substrates.AdmissionError as exc:
            # A typed refusal that escaped the tool path — reached, for example,
            # by `resources/list` over a kernel that refuses. Relayed with its
            # own code in `data` rather than flattened into a string.
            return _error_response(
                ident, ERR_INTERNAL,
                getattr(exc, "message", None) or str(exc),
                {"code": getattr(exc, "code", None),
                 "detail": getattr(exc, "detail", None) or {}})
        result = dict(result)
        result["resultType"] = "complete"
        meta = dict(result.get("_meta") or {})
        meta[META_SERVER_INFO] = dict(SERVER_INFO)
        result["_meta"] = meta
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def _check_meta(self, params):
        """Enforce the stateless core's per-request metadata.

        `2026-07-28` makes `protocolVersion` and `clientCapabilities` REQUIRED on
        every request and says a request missing either is malformed and MUST be
        rejected with `-32602`. Enforcing it is not pedantry: the whole reason
        the handshake could be deleted is that these fields are on every request,
        so a server that tolerates their absence is a server that has quietly
        kept the handshake's assumptions without the handshake.
        """
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            raise McpError(
                ERR_INVALID_PARAMS,
                "every request must carry _meta with %s and %s"
                % (META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES),
                {"required": [META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES]})
        version = meta.get(META_PROTOCOL_VERSION)
        if not isinstance(version, str):
            raise McpError(
                ERR_INVALID_PARAMS,
                "_meta[%r] is required on every request" % META_PROTOCOL_VERSION,
                {"required": [META_PROTOCOL_VERSION]})
        if version not in MCP_SUPPORTED_VERSIONS:
            raise McpError(
                ERR_UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": list(MCP_SUPPORTED_VERSIONS),
                 "requested": version})
        if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
            raise McpError(
                ERR_INVALID_PARAMS,
                "_meta[%r] is required on every request"
                % META_CLIENT_CAPABILITIES,
                {"required": [META_CLIENT_CAPABILITIES]})

    def _check_cursor(self, method, params):
        """Refuse a page position this server has no page to put it in.

        One check for every paginated method, driven off `PAGINATED_METHODS`
        rather than written into four handlers. Per-handler is where this goes
        wrong: a fifth list method arrives, its handler is written from the
        nearest neighbour, and the omission is invisible because the symptom of
        the omission is *a correct-looking response*.

        Refusing only where the spec puts a cursor — rather than refusing the key
        everywhere — keeps the message true. On `tools/list` a `cursor` is a
        parameter the protocol defines and this revision does not implement; on
        `tools/call` it is a parameter nobody defines, which is a different
        complaint and belongs to whatever checks that method's arguments.

        `None` is absence. The spec permits exactly one determination from a
        cursor value — whether a non-null one was provided — and reaching a
        different conclusion from `null` than from an omitted key would be a
        second determination, made against the only rule stated about the value.
        """
        if method not in PAGINATED_METHODS:
            return
        if PAGINATION_PARAM not in params:
            return
        cursor = params[PAGINATION_PARAM]
        if cursor is None:
            # Explicitly "no cursor". The same request as one that omitted it.
            return
        raise McpError(
            ERR_INVALID_PARAMS,
            "pagination is not supported by this server revision: %s takes no "
            "%s. This server never returns a nextCursor, so any cursor sent to "
            "it names a position it did not issue." % (method, PAGINATION_PARAM),
            {"method": method,
             "param": PAGINATION_PARAM,
             "supportsPagination": SUPPORTS_PAGINATION,
             "paginatedMethods": list(PAGINATED_METHODS)})

    def _dispatch(self, method, params):
        if method == "server/discover":
            return self.describe()
        if method == "tools/list":
            return {"tools": self.list_tools(), "ttlMs": TTL_CATALOG_MS,
                    "cacheScope": SCOPE_PUBLIC}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise McpError(ERR_INVALID_PARAMS,
                               "tools/call needs a string name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            return self.call_tool(name, arguments)
        if method == "resources/list":
            return {"resources": self.list_resources(), "ttlMs": TTL_CATALOG_MS,
                    "cacheScope": SCOPE_PUBLIC}
        if method == "resources/templates/list":
            return {"resourceTemplates": self.list_resource_templates(),
                    "ttlMs": TTL_CATALOG_MS, "cacheScope": SCOPE_PUBLIC}
        if method == "resources/read":
            return self.read_resource(params.get("uri"))
        if method == "prompts/list":
            return {"prompts": self.list_prompts(), "ttlMs": TTL_CATALOG_MS,
                    "cacheScope": SCOPE_PUBLIC}
        if method == "prompts/get":
            return self.get_prompt(params.get("name"), params.get("arguments"))
        raise McpError(ERR_METHOD_NOT_FOUND, "Method not found: %s" % method,
                       {"method": method})


def _error_response(ident, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def open_adapter(package, split, output, **kwargs):
    """Admit the environment and wrap it in the MCP vocabulary.

    One line of its own logic. `ors.open_adapter` performs the entire ruled
    startup admission — reopen the package, re-derive every id from the bytes,
    resolve the split, bind the subject tree, build one registry, build one
    kernel, prove the output root writable — and this adds the wire vocabulary
    over the result. There is no second admission path, which is the point: a
    second transport that admitted differently would be a second server.
    """
    adapter = _ors.open_adapter(package, split, output, **kwargs)
    return McpAdapterV1(adapter, output=os.path.abspath(output))
