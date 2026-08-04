# Residency MCP Profile v1 — memo

**Slice:** `trvs serve --mcp`. Give the Episode Kernel its **second** transport:
the same kernel, the same admission, the same receipts, behind the Model Context
Protocol's wire vocabulary (tools / resources / prompts).
**Status:** shipped, with one ruled law expired and a re-scoping *proposed, not
applied* (§12).

**MCP specification revision: `2026-07-28`.** Named in `mcp.MCP_PROTOCOL_VERSION`,
refused by name if a client asks for anything else, and asserted by M1. A
transport that does not name its protocol revision is making an unfalsifiable
claim — "it supports MCP" cannot be wrong, because there is no stated thing to
check it against.

**Battery.** M1–M31: **31 passed / 0 skipped / 0 failed**. `test_ors.py`:
**29 passed / 1 failed** — the one failure is O25, and §12 argues it is an
expired clause rather than a regression, with the observed evidence.
`test_kernel.py` + `test_cli.py`: **40 passed / 0 failed**.

---

## 1. What a second transport is for

The kernel was extracted before the first transport so that the transport would
have to translate into it rather than define it. The ORS memo §1 claimed that
worked. But one transport cannot actually test that claim: with a single caller,
"the kernel is transport-neutral" and "the kernel is shaped exactly like HTTP"
predict the same code.

A second transport is the experiment. It differs from the first on every axis
that could have leaked: JSON-RPC instead of REST, a stateless protocol instead of
a session-bearing one, pipes instead of sockets, a language model as the caller
instead of a script.

The result is legible in what this slice did **not** have to touch. No change to
`kernel.py`. No change to `ors.py`. No change to `identity.py`, `evalone.py`,
`execfacts.py` or `episode_bundle.py`. No new identity rung, no new receipt
field, no new CLI verb, no schema migration, and **not one byte of difference in
any episode**: an MCP episode and an ORS episode over the same candidate are the
same `episode-…`, because the thing that happened is the same thing.

The one file that changed is `cli.py`, and §12 is about the single law that
noticed.

## 2. The revision matters more than usual

`2026-07-28` is not a routine bump. It is the release that made MCP **stateless**:

- the `initialize` / `notifications/initialized` handshake was **removed**;
- protocol-level sessions and the `Mcp-Session-Id` header were **removed**;
- every request now carries `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities` in `_meta`;
- `server/discover` became a MUST;
- every result carries `resultType`;
- list results carry `ttlMs` and `cacheScope`;
- SSE resumability and message redelivery were **removed** — a broken stream
  means the client re-issues with a **new request id**;
- `ping`, `logging/setLevel` and `resources/subscribe` were removed;
- `-32020`–`-32099` was reserved for the spec; resource-not-found moved from
  `-32002` to `-32602`.

And, decisively for this slice, the spec now says what a server with state should
do instead:

> Servers that need cross-call state use explicit, server-minted handles passed
> as ordinary tool arguments.

That is the ORS session model, arrived at independently by the protocol. The
kernel's `session-…` — minted from randomness, never entering a receipt, worth
exactly one scoring — is *already* the thing `2026-07-28` asks for. This mapping
was found, not designed.

Building against a remembered revision would have produced a server with an
`initialize` handshake, a session header, and a `tools/call` that assumed prior
state: three things this revision deleted. That is why the research came first.

## 3. Tools vs resources vs prompts — the argument, not the enumeration

The three vocabularies are not three ways to say the same thing. They differ in
*who drives* and in *what happens when you do it twice*.

**Resources are the content-addressed and immutable.** Reading one twice returns
the same bytes and has no effect. That is exactly what an `env-…`, a `task-…` and
a published `episode-…` are: names *derived from* the bytes they return. They get
a `trvs://` scheme and a one-hour `ttlMs`, and the long ttl is **earned by content
addressing** rather than guessed — a different receipt would have a different
name, so staleness is not merely unlikely, it is unrepresentable (M6, M12).

**Tools are what changes or spends something.** `submit_candidate` consumes a
chance that does not come back. That is the opposite of a resource read in every
respect that matters.

**A session is neither, and that is the sharpest line here.** It is mutable,
ephemeral, and derived from randomness rather than content — `SESSION_PREFIX`
exists so that is visible at a glance. Publishing `trvs://session/<id>` would put
a thing with *none* of a resource's properties into the namespace whose entire
meaning is those properties, and a client would reasonably cache it. M13 pins
that the resource space does not name sessions at all.

**Prompts are user-driven templates**, so there is one: `residency_task`, taking
a `task_id`. One prompt per task was rejected because the *template* does not
vary — only its data does, and data that varies belongs in an argument. M15 pins
that its text is `ors.task_prompt` byte-for-byte; a second copy here would be a
second thing that could drift, and the drift would be invisible because both
would look like reasonable instructions.

## 4. Few coarse tools, because the substrate has one operation

The ecosystem convention (Graphonomous's five loop-phase machines) is coarse
tools. Here the question answers itself for a different reason: Residency has
**one** operation with any content in it.

    list_tasks        a read
    open_session      the handle's constructor — explicit, because the protocol
                      no longer has sessions of its own
    submit_candidate  the episode
    close_session     the handle's destructor

There is no design freedom to spend. A second scoring tool would have to *mean*
something, and this substrate has nothing for it to mean. ORS reached one tool by
the same road; MCP needs three more only because a stateless protocol makes the
handle's lifecycle explicit, which is a property of the wire and not of the
substrate.

## 5. The refused operations: unlisted, but still answered

`observe` / `step` / `reset` are **absent from `tools/list`** and **still routable
by `tools/call`** (M10, M11). Both halves are deliberate and they pull in
opposite directions.

Not listed, because `tools/list` is handed to a language model as a menu. ORS §2's
rule — *an advertised capability is a claim* — is strictly sharper here than it
was over HTTP. A client's retry loop discovers a bad claim at 3am; a **model**
discovers it immediately and repeatedly, because the menu said the operation
exists. Advertising `step` would be paying that cost on every turn.

Still routable, because if they were simply absent, calling one would produce
"unknown tool" — a claim about *this server's catalog*. The truth is a claim about
the **substrate**, and the client deserves to be told which. So the rule is:

| the name is… | answer |
| --- | --- |
| in the catalog | the tool runs |
| in `kernel.OPERATIONS` but not the catalog | the kernel's own `KERNEL_OPERATION_UNSUPPORTED`, relayed verbatim, as `isError: true` |
| in neither | `-32602 Unknown tool` |

The middle row is ORS's 501 argument transplanted. The transport owns no part of
that refusal — on the day a substrate learns to step, this code says so without
being edited.

## 6. Idempotency: refused, not replayed — and the reason is the wire, not taste

ORS put an `Idempotency-Key` in an HTTP **header** because retry safety is a
property of the wire. This transport adds **no key at all**, and that is the same
principle applied to a different wire rather than a reversal of it.

HTTP retries happen *below the caller*: a proxy, a reset connection, or a client
library can re-send a POST with the caller unaware. A key is needed to stop a
machine-generated duplicate from becoming a second episode. Under `2026-07-28`
the opposite is specified — resumability and redelivery were **removed**, and a
client whose stream breaks "MUST re-issue it as a new request with a new request
ID". Retries are explicit, caller-driven, and already distinguishable.

An `idempotency_key` **tool argument** would have been worse than nothing. Tool
arguments are written by a language model, and a key a model invents is not an
idempotency key — it is a token that makes double-execution *look* deduplicated.
By ORS §3's own logic it is also client-supplied content that steers server
behaviour, which is the category `RemoteSubmissionV1` exists to keep empty.

So the honest answer is the one the kernel already gives. Passing
`idempotency_key=None` into the ORS adapter makes ORS's own **O21** rule ("a
keyless second submission is refused") the MCP rule, with no new mechanism and no
cache. M20 asserts the keyword is a **literal** `None`, parsed from the AST — a
computed one would make this uncheckable by any means.

### The one thing refusing costs, and how it is repaid

Refusing rather than replaying costs recoverability: a client that lost the
response cannot get its reward back. That is repaired **without** reintroducing a
cache. The `ORS_SESSION_FINISHED` refusal is enriched with the `episode_id` this
handle produced and a `trvs://episode/…` link (M19).

It is still a refusal — `isError: true`, no `reward` key, the handle stays spent.
It simply points at the evidence, which is on disk because `finished: true` was
only ever returned after `write_episode_bundle` staged, replay-verified, fsynced
and renamed it. The adapter's `_spent` dict holds **episode ids, never results**,
and M20 asserts that: a dict holding results would be a cache wearing a
signpost's clothes.

## 7. Two error channels, one dividing line — the same line ORS drew

MCP has JSON-RPC protocol errors and `CallToolResult.isError`, and they mean
different things. The question that sorts them is the one ORS answered with HTTP
status codes: **is this the caller's to fix?**

| refusal | channel | why |
| --- | --- | --- |
| `KERNEL_OPERATION_UNSUPPORTED` | `isError: true` | names the alternatives; a model can call `submit_candidate` instead. A protocol error is more likely to be swallowed by the client and never shown to the model — which would lose ORS's "in the substrate's words" property at the last step |
| `KERNEL_SESSION_STATE` | `isError: true` | actionable: open a new session |
| `KERNEL_SESSION_BUSY` | `isError: true` | actionable: the close can be retried |
| `ORS_SUBMISSION_*`, `MCP_ARGUMENT_*` | `isError: true` | the spec lists input validation errors as tool execution errors, and this is the most model-correctable thing there is |
| `ORS_SESSION_FINISHED` | `isError: true` | actionable, and carries the link (§6) |
| `KERNEL_RUN_RESULT_MISSING` / `_UNEXPECTED` | `-32603` | **a client cannot cause it.** This adapter builds the `RunResult`; the client never supplies or withholds one. If it reaches a client, this server has a defect, and offering it to a model as self-correctable invites it to fix an input it does not have |
| `ORS_PUBLISH_FAILED`, `ORS_NO_EVIDENCE` | `-32603` | the disk is not something a model can argue with, and each retry re-runs a verifier plan |
| unsupported version | `-32022` | with `data.supported`, because that field *is* the client's fallback mechanism |
| missing `_meta` field | `-32602` | a MUST |
| unknown method / unknown tool | `-32601` / `-32602` | chosen by the client library, not the model |

The mapping is stated as **data** (`_PROTOCOL_ERRORS`), for the same reason
`ors_server._STATUS` is: prefix is not the question. `ORS_SESSION_FINISHED` and
`ORS_PUBLISH_FAILED` share a prefix and are opposite answers.

One line is worth stating separately. A non-object `arguments` fails the
*CallToolRequest schema* and is a protocol error; a bad **submission** fails the
*tool's* `inputSchema` and is a tool error. The spec draws exactly that line and
M22 pins both sides of it.

## 8. The trust boundary survived translation, and the nesting is why

This is the law the design turns on (M16, M17).

`RemoteSubmissionV1` is validated by **exact key set** — not "these are required",
but "this is the document". So `submit_candidate` takes the submission as a
**nested object**:

```json
{"session_id": "session-…", "submission": {"submission_version": …,
                                           "finding": …, "patch": …}}
```

Folding `session_id` in beside `finding` and `patch` would have forced either a
fourth allowed key or a strip-then-validate step, and **both turn "is this the
document?" back into "does this contain the document?"**. The handle is transport
framing and stays outside the document — which is the same argument that put
ORS's idempotency key in a header rather than in a body.

Both sides are enforced. A `session_id` *inside* the document is an unknown field
to the exact key set; a missing one *outside* is `MCP_ARGUMENT_MISSING`. And an
argument nobody declared — `idempotency_key`, say — is `MCP_ARGUMENT_UNKNOWN`
rather than silently ignored.

`MCP_ARGUMENT_*` are raised as a **new** typed class, `McpRefusal`, rather than as
`OrsError`. `ors.py` owns the vocabulary for a bad *submission*; this owns the
vocabulary for a bad *call*. Reusing `OrsError` would have grown the ORS refusal
vocabulary with names meaningless to an ORS client, and O30 — the law that pins
that vocabulary — would have had to be loosened to admit them.

## 9. Concurrency: the transport must not undo what the kernel bought

The obvious stdio server is a loop: read a line, handle it, write the answer.
It is wrong for this kernel, and wrong in a way that is invisible until it
matters.

`2026-07-28` states clients may interleave unrelated requests and that an open
stdio process "is not a conversation or session". The Finalize Linearization
Closure (K25, K26) established that two *different* sessions must be able to be
inside the scoring work simultaneously. A read-handle-write loop reintroduces
exactly the serialization K15/K16 exist to prevent, one layer up: the kernel
stays linearizable and the **server** scores one episode at a time, so the
property is preserved on paper and lost in fact.

So each request is dispatched on its own daemon thread and only the **writes** are
serialized, under a lock held just long enough to put one complete line on the
stream.

The three laws are the kernel's K19–K26 seen through a second wire:

| law | statement |
| --- | --- |
| M24 | two different sessions score **simultaneously** over real pipes — a `threading.Barrier` inside `_finish_episode` that a serializing transport would **deadlock**, not merely slow |
| M25 | two submissions to one handle: exactly one scores, the other is refused by name, and the scoring work **runs once** (counted, not inferred) |
| M26 | neither adapter holds a lock across scoring — proven under load, with a whole second request driven to completion while the first is suspended |

M25 counts *entries into the work* rather than successes, because "exactly one
submission succeeded" is strictly weaker than "the work ran once" — the defect
the linearization slice fixed satisfied the first while violating the second.

M27 covers what this transport genuinely owns: framing. A pipe write above
`PIPE_BUF` is not atomic, so two concurrent responses without a lock interleave
into lines that are not JSON at all. Checked empirically (12 concurrent
requests, every line parsed standalone) **and** structurally, because a load test
can only ever fail to find the bug.

## 10. Loopback by default was the wrong question; stdio has no surface at all

ORS chose loopback with a deliberately blunt `--allow-remote`, because a server
holding candidates' patches and running verifier commands should not be reachable
by accident. Stdio is strictly better on that axis: **there is no network surface
at all.** The client is the process that launched this one, the channel is a pipe
it already owns, and there is no port, no bind address, no origin check, and no
flag that could expose one. `--allow-remote` has no meaning here and deliberately
does not exist.

M28 proves that structurally — `mcp_server` names no `socket`, `bind`, `listen`,
`http`, `urllib` or `ssl` — and does it by **parsing**, because the module's own
docstring explains at length that it has no socket and no port, and a text scan
would read those very sentences as evidence that it does.

`--mcp` **refuses** `--host` / `--port` / `--allow-remote` rather than ignoring
them. A flag silently doing nothing is how somebody comes to believe an stdio
server is listening on 9000.

### Streamable HTTP is not implemented, and that is a decision

Under `2026-07-28` a conforming Streamable HTTP server owes standard request
headers (`Mcp-Method`, `Mcp-Name`, `MCP-Protocol-Version`), origin validation,
the authorization framework, `x-mcp-header` parameter mirroring, and
`subscriptions/listen` as a long-lived POST-response notification stream. A
partial implementation would advertise a transport this server does not actually
speak — the same species of false claim as a tool catalog listing an operation
the substrate refuses.

The kernel already has an HTTP surface: `trvs serve --ors`. A client that wants
one should use it. This is deferred, not skipped, and it is named in §14.

## 11. Cancellation, and the claim this server refuses to make

`notifications/cancelled` is honoured to the letter of what is achievable and no
further. The spec requires no further messages for a cancelled request and says
work SHOULD stop "as soon as practical". This server **suppresses the response and
does not stop the work**, because it cannot: the kernel has no cancel, `finalize`
is a one-shot claim, and interrupting a verifier plan mid-flight would leave the
session `finalizing` forever — un-closeable by `KERNEL_SESSION_BUSY` and
un-scorable by anyone. Killing the work to look responsive would trade a slow
answer for a permanently wedged session.

M31 pins the honest statement: **a cancelled submission loses its report, not its
result.** The episode is still published, and the next submission on that handle
is refused with a link to it — the same recovery §6 provides, reached from a
different direction.

## 12. A law that expired, and one that did not — with the evidence

Two `test_ors.py` laws inspect the CLI. Adding a second protocol to `serve` made
one of them fail. They needed opposite treatments, and telling which was which
required running the command rather than reading the parser.

### O26 did **not** expire — and the first version of this slice broke it

O26 asserts the ORS bind default is loopback, by reading `--host`'s
`action.default`. The first version of this slice defaulted `--host` to `None` and
resolved `"127.0.0.1"` inside `_serve_ors`, so that `--mcp` could tell "the
operator typed `--host`" from "the operator did not".

The safety property survived that — but the law was right to fail anyway, and the
reason is worth keeping: **"what does this bind to by default?" must be answerable
by reading the parser**, because that is where anyone looks and what `--help`
prints. Hollowing the default out to `None` moved a ruled safety default into a
function body where nobody would find it.

The fix keeps the default on the action and records explicitness beside it, via a
small `argparse.Action` subclass (`_Explicit`) that notes which options were
actually supplied. O26 now passes untouched.

Observed evidence that the property is true of the *shipped command*, not merely
of the parser — `trvs serve pkg --ors --split all --output … --port 8873`, with no
`--host`:

```text
$ ss -ltnp | grep 8873
LISTEN 0 5   127.0.0.1:8873   0.0.0.0:*   users:(("python3",pid=2287283,fd=3))

connect 127.0.0.1       -> OPEN
connect 192.168.1.148   -> REFUSED (ConnectionRefusedError)     # host's LAN address

$ trvs serve pkg --ors … --host 0.0.0.0        # no --allow-remote
trvs: [ORS_REMOTE_BIND_REFUSED] refusing to bind '0.0.0.0': … pass --allow-remote
      if that is what you mean                                        exit 2
```

### O25 **did** expire, exactly as K18's clause did

O25's last clause asserts `{"ors", "split", "output"} <= required`, where
`required` is the set of individually-required argparse actions. `--ors` was an
individually required flag **while ORS was the only protocol this verb spoke**.
It cannot remain one now: argparse forbids a required member inside a mutually
exclusive group, and `serve-mcp` as a separate verb is forbidden by O30.

This is the same shape as the ORS memo §12: K18 asserted no CLI verb reached the
kernel, shipping ORS made that false *correctly*, and the clause was **re-scoped
rather than deleted**. What must not expire here is that the operator **chooses** —
neither protocol is defaulted, and `--output` is still mandatory because
`finished: true` is a durability claim.

The property is now **stronger**, not weaker: the protocol flags live in a
mutually exclusive group that is itself `required=True`, so exactly one of
`--ors` / `--mcp` must be given and giving both is an error.

**Proposed replacement for the last clause of O25 — not applied.** This edit is
left for the coordinator so that a change to a ruled law is visible in one place
rather than buried in a transport slice. It has been executed and passes:

```python
    # And the CLI makes it required rather than defaulted, because a default
    # would be a directory the operator did not choose to fill with evidence.
    from traaviis import cli
    parser = cli.build_parser()
    serve = parser._subparsers._group_actions[0].choices["serve"]
    required = {a.dest for a in serve._actions if getattr(a, "required", False)}
    assert {"split", "output"} <= required, required
    # `--ors` was an individually required flag while ORS was the only protocol
    # this verb spoke; `trvs serve --mcp` shipped, so that clause expired -- the
    # same way K18's "no CLI verb reaches the kernel" expired when ORS shipped.
    # What must not expire is that the operator *chooses*. The protocol flags
    # are a mutually exclusive group that is itself required, so exactly one of
    # --ors / --mcp must be given and neither is defaulted -- which is a
    # stronger statement than the one this clause used to make.
    groups = [g for g in serve._mutually_exclusive_groups if g.required]
    assert len(groups) == 1, "the protocol choice must be a required group"
    assert {a.dest for a in groups[0]._group_actions} == {"ors", "mcp"}
```

Until it is applied, `test_ors.py` is **29 passed / 1 failed**.

## 13. Two corrections this slice made to its own laws

Both were found by running the battery, and both are the *same* failure this
codebase keeps rediscovering.

**M20 grepped where it should have parsed.** Its first form scanned each
serialized tool for the substring `idempot` and failed on `close_session`, whose
description correctly says the operation *is idempotent*. That is a true sentence
about the tool, not an argument. This is the **seventh** occurrence of the rule
K10, K12, K18, K28 and O30 each had to learn: *a module is allowed to name a thing
it deliberately does not do*, so a law about structure has to parse rather than
grep. M20 now inspects schema **property names**; M28 uses a shared
`_code_identifiers()` helper that walks the AST and cannot see string constants at
all.

**M18 asserted a receipt shape it had not read.** It expected
`execution_facts["runner_profile"]` and `["termination"]` at the top level; the
sealed shape is `["runner"]["profile"]` and `["agent_process"]["termination"]`.
The law was written from the memo's prose rather than from a receipt. It now
reads the real one, and asserts the two distinctions that prose was making:
`not_executed` is not `exited`, and `not_applicable` is not `none`.

**A defect in this slice's own fixture, inherited by copy.** `_task()` was copied
from `test_ors.py` and carried `"PATH": os.environ.get("PATH", "")` into
`agent_run_policy.environment`. That is the leak just closed in `test_kernel.py`
(K27): `agent_run_policy` is inside `task-`, and `task-` is inside `episode-`, so
an ambient `PATH` moves the episode identity while changing nothing the identity
describes — and `runner._seal_env` discards it, so it never reached a child
anyway. Under this transport nothing is executed at all, which makes it pure
noise in a name. Removed. This battery pins no episode id, so it was latent
rather than failing — which is precisely why it was worth removing before it
became load-bearing.

## 14. Autonomous decisions (flagged for review)

Nothing here was ruled — the ruling deferred the whole slice — so the whole design
is autonomous. These are the choices a reviewer should look at first.

1. **`mcp.py` composes `ors.OrsAdapterV1` rather than reaching the kernel
   directly.** `ors.py` is misnamed by history: its own docstring calls itself
   transport-independent, with `ors_server` as "a thin encoding of it". It is the
   Residency **submission semantics**; HTTP lives next door. Going to the kernel
   directly would have required duplicating `trusted_run_result` — creating a
   **second** place that decides what the server will believe a client did, which
   is the exact failure the extraction was performed to prevent. M30 pins that
   `mcp.py` names no `identity`, `reward`, `RunResult`, `build_receipt_v1`,
   `write_episode_bundle`, `finalize` or `KernelError`.
2. **The refused operations are routable but unlisted** (§5). The alternative —
   listing them so `tools/list` is a complete map of `kernel.OPERATIONS` — puts a
   known-failing entry on a model's menu every turn.
3. **No idempotency key at all** (§6). The most consequential decision here, and
   the one most likely to be contested. If it is overturned, the key belongs on a
   server-minted handle, never on a model-written argument.
4. **The refusal carries `episode_id` + `episode_uri`.** The `_spent` dict is new
   state in the transport. It holds ids only, never results, and M20 asserts that.
5. **Resources are scoped to the environment, the tasks and published episodes.**
   Reward specs and snapshots are *not* exposed, though ORS holds them: serving
   them would make this module a second, partial implementation of
   `verify-episode`'s reader, which has an opinion about closure that a resource
   read must not quietly approximate. They are inside the episode bundle, which
   is the artifact that actually needs to be closed.
6. **`resources/read` on an episode serves `receipt.json` only**, and refuses if
   the directory name and the sealed `episode_id` disagree, rather than serving a
   receipt under a name it does not claim.
7. **A custom `_meta` key `com.traaviis/profile`** carries the profile, kernel,
   runner and env ids on `server/discover`. Under this project's own reverse-DNS
   prefix, because `_meta` reserves anything whose second label is
   `modelcontextprotocol` or `mcp`. A client that does not know TRAAVIIS ignores
   it.
8. **`ttlMs` of one hour and `cacheScope` of `public` for catalogs, `private` for
   episodes.** The scope split is about *whose content it is*, not how it is
   addressed: an episode carries a submitter's finding and patch.
9. **Capabilities are declared empty** — no `listChanged`, no `subscribe`. The
   catalog is frozen at admission; there is no notification this server could
   honestly emit.
10. **Threads are unbounded.** On stdio the peer is the parent process, which
    already has strictly more authority than any request it could send, and a
    queue limit would stall the reader that must stay live to receive
    cancellations.
11. **`_Explicit`, and the ORS defaults staying on the action** (§12).
12. **`drain()` waits for in-flight requests after EOF.** An episode in flight
    when the client closed the pipe is still going to be published; exiting out
    from under `write_episode_bundle` would abandon a scored episode mid-sequence.
    The client is gone and will never read the answer; the evidence is the part
    worth waiting for.

## 15. Live end-to-end

A fresh scaffold, no fixtures, driven by a real MCP client over a real pipe:

```text
trvs init env --template evidence-residency     8 files
trvs pack env pkg                                env-a38ec4c04532be25…
                                                 snap-c66198ab… task-3b4b2599…
                                                 rew-25c4ce12… bundle-214fb799…
trvs serve pkg --mcp --split all --output episodes-mcp

server/discover                supportedVersions ["2026-07-28"]  resultType complete
tools/list                     list_tasks, open_session, submit_candidate, close_session
                               ttlMs 3600000 / cacheScope public
resources/list                 trvs://env/env-a38ec4c0…   trvs://task/task-3b4b2599…
resources/templates/list       trvs://episode/{episode_id}
prompts/get residency_task     "Task task-3b4b2599…"
tools/call open_session        session-d20f6c46748746cbbfca1f22b40b8234
tools/call submit_candidate    isError false  status ok  validity valid
                               reward 1.0  finished true
                               episode-29690223ea13acab06c04e551ecbb171…
                               resource_link trvs://episode/episode-29690223…
  (repeat, same handle)        isError true  ORS_SESSION_FINISHED
                               no reward; points at trvs://episode/episode-29690223…
tools/call step                isError true  KERNEL_OPERATION_UNSUPPORTED
tools/call frobnicate          -32602  Unknown tool: frobnicate
tools/list @ "2025-11-25"      -32022  {"supported":["2026-07-28"],"requested":"2025-11-25"}
resources/read the episode     cacheScope private; receipt reward 1.0
                               runner traaviis.ors-submission.v1  termination not_executed
close stdin                    server exit 0

trvs verify-episode episodes-mcp/episode-29690223ea13acab06c04e551ecbb171…
  closure       ✓ all members bind
  signals       7/7  replay == receipt
  reward        ✓ replayed reward 1.0 vs receipt 1.0
  episode-id    ✓ episode-id closes over the derived receipt
  verified      ✓ closed
```

The last line is the point of the whole stack, and it is the *second* transport to
reach it: the episode a language-model client produced over a pipe replays
offline, with no agent, no server and no protocol, to the same reward.

The banner is on **stderr**, every line of it. Over HTTP the banner is output;
here stdout is the wire, and a decorative line on it is not a cosmetic problem —
it is a malformed message from a server the client otherwise trusts.

## 16. Cost

Two new modules (`traaviis/mcp.py` 1012, `traaviis/mcp_server.py` 291), one new
battery (`test/test_mcp.py` 1578, M1–M31), and `cli.py` +168/−25 (a mutually
exclusive protocol group, `_Explicit`, `_admission_inputs`, and `cmd_serve` split
into `_serve_ors` / `_serve_mcp`).

**No new dependency** — `json`, `sys`, `threading` are standard library, as the
kernel and the ORS transport are. No `mcp` pip package. No new identity rung, no
new receipt field, no schema migration, no change to `kernel.py`, `ors.py`,
`identity.py`, `evalone.py`, `execfacts.py` or `episode_bundle.py`, and **no
change to any existing episode**.

## 17. Unproven, and not started

**Unproven — stated as unproven, not as working:**

- **No third-party MCP client has connected.** The live demonstration is driven by
  a client written for it (`mcp_client.py`, ~180 lines, imports nothing from
  traaviis and knows only the wire). The framing, the `_meta` contract, version
  negotiation, both error channels and the shutdown signal are all exercised
  against a real subprocess over real pipes — but interoperability with a shipped
  SDK or with Claude Desktop is **not** demonstrated. `2026-07-28` is recent and
  Tier-1 SDK support for the stateless core may lag; a client still speaking
  `2025-11-25` will receive `-32022` and, per the spec, has no fall-forward.
- **Backward compatibility with legacy (`initialize`-based) clients is not
  implemented and not tested.** This is a modern-only server. A legacy client gets
  `-32601` on `initialize`. The spec notes such a server SHOULD name its supported
  versions in that error; it currently does not.
- **Pagination is not implemented.** `nextCursor` is never returned. Correct for a
  catalog of one environment and a handful of tasks; untested for a package large
  enough to need it, and a client that *sends* a `cursor` is silently ignored
  rather than refused.
- **`MAX_LINE_BYTES` is not exercised.** An 8 MiB line cap exists; no law drives a
  message to it.
- **Windows.** `os.pipe`, `readline` framing and EOF shutdown are POSIX-tested
  only.

**Not started (deferred):**

- **Streamable HTTP** for MCP — deferred with the reason in §10, not skipped.
  `subscriptions/listen`, `Mcp-Method` / `Mcp-Name` headers, origin validation and
  the authorization framework come with it.
- **The `io.modelcontextprotocol/tasks` extension.** Scoring is fast here; a
  long-running substrate would want it.
- **`notifications/progress`.** A verifier plan that executes a test suite could
  report progress; nothing does yet, and `_meta.progressToken` is ignored.
- **Authentication.** Neither transport has any. Stdio does not need it; ORS's
  loopback default is still the answer there.
- The REPL, `EvaluationV2`, `eval-…`, `agent-…`, and batch-evidence distribution
  identity remain deferred.
