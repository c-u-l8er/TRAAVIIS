# Residency MCP Profile v1 — memo

**Slice:** `trvs serve --mcp`. Give the Episode Kernel its **second** transport:
the same kernel, the same admission, the same receipts, behind the Model Context
Protocol's wire vocabulary (tools / resources / prompts).
**Status:** shipped. ~~with one ruled law expired and a re-scoping *proposed, not
applied* (§12)~~ — **superseded: the §12 re-scoping has been applied**, and O25
passes on it. §12 is kept as the record of the argument, which does not expire
with the edit.

**MCP specification revision: `2026-07-28`.** Named in `mcp.MCP_PROTOCOL_VERSION`,
refused by name if a client asks for anything else, and asserted by M1. A
transport that does not name its protocol revision is making an unfalsifiable
claim — "it supports MCP" cannot be wrong, because there is no stated thing to
check it against.

**Battery.** M1–M40: **40 passed / 0 skipped / 0 failed** (~0.6 s). `test_ors.py`:
**31 passed / 0 failed**.

~~`test_ors.py`: **29 passed / 1 failed** — the one failure is O25, and §12 argues
it is an expired clause rather than a regression.~~ **Superseded.** The §12 edit
that §12 itself left *proposed, not applied* has since been applied, so O25 now
passes on its re-scoped clause; the battery also gained O31. The clause's
argument stands unchanged and is kept in §12 as the record of why the law was
re-scoped rather than deleted — what expired is the *count*, not the reasoning.
`test_kernel.py` + `test_cli.py`: **40 passed / 0 failed**.

The battery grew three times after the slice shipped, and every addition was a
review finding rather than a new feature. M32–M37 replaced the cancellation
mechanism and the EOF shutdown (§11 and §14.12, both recorded as superseded
rather than rewritten). M38–M39 close two accuracy defects: a `cursor` was
**ignored** where it should have been refused, and one resource description
promised a *bundle* where the read returns a *receipt* (§18). M40 closes a
release defect in the cancellation lifecycle M32–M35 introduced: `_run_one`'s
defensive second `_settle` was unconditional, so when two holders shared one id
a failed `_write` in the first released the second's hold — after which a
cancellation for the survivor was ignored and its answer written anyway (§19).

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

So each request is dispatched on its own thread and only the **writes** are
serialized, under a lock held just long enough to put one complete line on the
stream.

(This sentence said **daemon** thread until 2026-08-04, and it was accurate when
written. The threads are no longer daemon threads, and the reason is a shutdown
property rather than a concurrency one — see §14.12. Nothing in this section
depends on which it is: the concurrency argument is about *dispatch*, and the
daemon flag governs only what the interpreter is permitted to kill at exit.)

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

### The mechanism described above is superseded — the claim above is not

**Superseded 2026-08-04.** Everything to this point still holds; M31 still passes
unchanged. What changed is the machinery underneath it, and the reason is worth
keeping rather than overwriting.

**What was believed.** The first version recorded every cancelled request id in a
permanent `set` and consulted it just before writing a response. The reasoning
was that the write is the last moment at which suppression is still possible,
which is true, and that a set of ids is the cheapest thing that could answer "was
this cancelled?", which is also true. The design was checked against the question
it asked.

**What was actually true.** The question was wrong. A permanent set makes
cancellation a fact about a **number**, not about a request in progress, and
three separate defects follow from that — only one of which was visible:

1. it accepted a cancellation for a request that did not exist;
2. it never released an id when the request finished;
3. and therefore — because JSON-RPC ids are chosen by the client and are
   routinely small integers — a `notifications/cancelled` for id `7` sent before
   any request `7` existed would silently suppress the response to **every** later
   request numbered `7`. The work ran, the episode published, and the answer was
   thrown away while the client waited forever for a message this server had
   already decided not to send.

The spec permits ignoring a cancellation for an unknown or already-completed
request. It does not permit remembering one.

**What replaced it.** A three-state lifecycle — `unknown → running → unknown`,
with `cancelled` as a branch that rejoins — held in a map from request id to
state, with `unknown` as both the initial and the terminal state. Admission
happens **on the reader thread, in stream order**, so a client's cancellation of
its own request cannot race the worker's start; `notifications/cancelled` is
handled on the reader for the same reason. `_settle` and `_cancel` contend for
one lock, which names a single commit point: the response is never both written
and suppressed, never neither, and **the id never survives its request**. Laws
M32–M35 pin those four properties; the argument in full is in
`mcp_server.py`'s module docstring, which is the design record for the mechanism
as this section is for the claim.

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

~~Until it is applied, `test_ors.py` is **29 passed / 1 failed**.~~ **Superseded:
it has been applied.** O25 now carries the re-scoped clause above and passes;
`test_ors.py` is **31 passed / 0 failed** (the other addition since is O31, an
undecodable request body — §19). The proposed edit is left printed above rather
than deleted, because it is the record of *what* was changed in a ruled law and
of the argument for changing it, and that record does not expire when the edit
lands.

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

    **Superseded 2026-08-04 — the argument stood, the implementation did not.**
    As written above, and as first shipped, this entry described behaviour that
    did not happen. `drain()` joined each worker with a **30-second deadline** and
    returned regardless, and the workers were **daemon** threads, so the process
    then exited and killed them. A verifier plan that legitimately takes longer
    than thirty seconds — which is most of them, on a real repository — was
    abandoned mid-publication by the *normal* shutdown path, having already spent
    the candidate's one-shot session. The paragraph and the code disagreed, and
    the paragraph was the one telling the truth about the intent.

    Raising the number was rejected as a repair: whatever constant were chosen,
    this module would be asserting a bound on how long a verifier plan may
    honestly take, and it has no basis for one. Two changes, neither of them a
    number:

    - **the workers are no longer daemon threads**, which is the load-bearing
      half — durability now depends on the interpreter's own shutdown, which
      joins non-daemon threads unconditionally, rather than on a wait completing;
    - **`drain()` has no deadline by default** (`timeout=None`, the *absence* of
      a bound, not a large one). A caller may still pass a finite timeout, but it
      is a **reporting** knob: it names the unfinished requests on stderr and the
      process still does not exit, because of the first change.

    The remaining cost is stated rather than hidden: a genuinely hung verifier now
    hangs the shutdown instead of silently discarding a scored episode. M36 and
    M37 pin both halves — M37 records the argument of the join `drain` actually
    performs, because reading the default proves what the code says and only
    recording the real call proves what it does.

    ~~"a hang is visible" was the whole of the stated cost.~~ **Superseded — it
    understated it**, and the understatement is on the operator's side of the
    boundary. The cost is not a visible wait: it is that **the process becomes
    unkillable by Ctrl-C**. SIGINT raises `KeyboardInterrupt` on the main thread,
    which can break out of the join in `drain` — but the *interpreter's own*
    shutdown then joins every non-daemon thread unconditionally, and that join
    takes no signal and honours no interrupt. So the second Ctrl-C does nothing
    either, and the operator's only remaining move is `SIGKILL` — which is
    exactly the abrupt termination mid-publication that `daemon=False` was chosen
    to prevent, now reachable only by a deliberate act instead of by default.

    The design is unchanged, because the trade it makes is still the right one: a
    shutdown the operator has to escalate is recoverable, and an episode
    abandoned between `fsync` and `rename` is not. What changes is that the price
    is now stated in the terms the operator will actually meet it in. The honest
    summary is *"an unkillable-by-Ctrl-C process"*, not *"a visible hang"*.
13. **A supplied `cursor` is refused rather than paginated or ignored**, on every
    paginated method, from an enumeration transcribed from the spec rather than
    read off the dispatch table (§18.1). The contestable half is that this
    *narrows* what the server accepts: a client that today sends a cursor and gets
    a usable answer will tomorrow get an error. That is the intent — the answer it
    gets today is not the one it asked for.
14. **`cursor: null` is treated as absence**, not as a cursor (§18.1). The
    alternative — refuse anything the key is present with, including `null` — is
    defensible on "the client shouldn't send the key at all", and was rejected
    because a typed client serialising an empty optional field is not making a
    pagination request. The empty string is refused, because the spec says it is a
    valid cursor.

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

Two new modules (`traaviis/mcp.py` 1145, `traaviis/mcp_server.py` 632), one new
battery (`test/test_mcp.py` 2450, M1–M40), and `cli.py` +168/−25 (a mutually
exclusive protocol group, `_Explicit`, `_admission_inputs`, and `cmd_serve` split
into `_serve_ors` / `_serve_mcp`).

~~1135 / 598 / 2333 with M1–M39~~ — superseded by the §19 round, which is the
only change since.

At the moment the slice first shipped those files were 1012 / 291 / 1578 with
M1–M31. The growth since is all review work: the cancellation lifecycle and the
EOF shutdown (`mcp_server.py`, roughly doubled, over half of it the docstring
that is now the design record for the mechanism — §11), the two accuracy findings
in §18, and the release defect in §19.

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
  enough to need it. **Amended 2026-08-04:** the second half of this entry used to
  read "and a client that *sends* a `cursor` is silently ignored rather than
  refused". That is no longer true — a supplied cursor is now `-32602` on every
  paginated method (§18.1, M38). Real, opaque cursors remain unimplemented and
  unbuilt; what changed is that the gap is now refusable instead of silent.
- **`MAX_LINE_BYTES` is not exercised.** An 8 MiB line cap exists; no law drives a
  message to it.
- **`_decode` still catches only `(ValueError, UnicodeDecodeError)`** around
  `json.loads`, and `RecursionError` is a `RuntimeError`. So a client line that is
  valid JSON but nested past the decoder's stack — 200 000 nested arrays is
  400 kB, well inside the 8 MiB cap, which bounds *length* and not *depth* — does
  not become the `-32700` parse error the surrounding code is written to produce.
  Same class as O31 and §19.3, **left open here on purpose**: the five sites fixed
  in §19.3 all had a caller holding a typed refusal to return, and this one is on
  the reader thread, where the consequence of the escape (what happens to the loop,
  and whether the connection survives) is a design question rather than a
  clause-widening. Added 2026-08-04; unrepaired, and stated rather than left to be
  rediscovered.
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

## 18. Two accuracy findings from external review (2026-08-04)

Both are cheap. Both are the same defect in different clothing: **this server knew
something the client could not find out.** Neither adds a capability; each turns a
silent one into a stated one.

### 18.1 A cursor was ignored where it should have been refused

`cursor` appeared nowhere in `mcp.py`. A client that sent one received page one of
a single-page result — **byte-identical** to the response it would have received
had the cursor been honoured and the page been the last, because the two differ in
no field. So "your cursor was discarded" was not merely undocumented; it was
*unobservable*. A divergence that cannot be detected cannot be refused, which is
the whole property this codebase is built to have.

A supplied `cursor` is now `-32602`, which is the code the pagination spec names
for one ("Invalid cursors **SHOULD** result in an error with code -32602"). The
claim is stronger than "invalid": the only way to obtain a cursor is a
`nextCursor`, and this server emits none, so every cursor it can be sent is one
the client invented. Refusing the class costs a well-behaved client nothing.

**The methods** — `resources/list`, `resources/templates/list`, `prompts/list`,
`tools/list` — are transcribed from the specification's "Operations Supporting
Pagination" list into `mcp.PAGINATED_METHODS`, deliberately **not** derived from
this module's dispatch table. A set read off the code would describe what this
server implements while claiming to describe MCP. The check runs **once, over the
enumeration**, rather than in four handlers: per-handler is where this goes wrong,
because a fifth list method's omission shows up as a *correct-looking response*.
M38 closes the loop from the other end too — it parses `_dispatch` and asserts
every `/list` method it routes is in `PAGINATED_METHODS`, so the coverage cannot
silently go stale when a method is added.

**`cursor: null` is absence**, and M38 pins the strong form: the result is not
merely successful, it is *identical* to the result of the request that omitted the
key. The justification is the spec's own — it permits exactly one determination
from a cursor value, "whether a non-null value was provided", and reaching a
different conclusion from `null` than from an omitted key would be a second
determination made against the only rule stated about the value. It is also the
same sentence that makes `""` a real cursor, so the empty string is refused with
everything else.

The boundary is additionally **declared**: `com.traaviis/profile` on
`server/discover` gains `supports_pagination: false`. Under this project's own
prefix, not as an MCP capability — the schema has no flag for pagination, and
inventing one under the reserved prefix would be a claim in somebody else's
vocabulary. M38 asserts the declaration and the refusal are the same fact, because
a stated boundary that could drift from the enforced one is worse than none.

Real pagination is **not** implemented and was not attempted. Refusing now and
implementing opaque cursors later is the right order: the refusal is compatible
with any future cursor format, whereas a cursor format shipped to serve four
tasks is a format that has to be lived with.

### 18.2 A description promised a bundle where the read returns a receipt

`mcp.py` described the `resource_link` returned by `submit_candidate` as *"the
published, content-addressed episode bundle — replay it offline with `trvs
verify-episode`"*, while the resource template correctly titled the same URI
*"Published episode receipt"* and `resources/read` returns the receipt.

These are different artifacts and the difference is not pedantic.
`write_episode_bundle` produces a **directory** — `episode-bundle.json`,
`receipt.json`, `task.json`, `reward.json`, `snapshot.json`,
`evidence/trace.json`, `evidence/verifiers/*.json`, `evidence/finding.json`,
`evidence/process/policy-violations.json` — and the receipt is *one document
inside it*. Offline replay reads the directory. So the sentence told a language
model that reading one URI gets it everything it needs to replay, and it does not.
**Offline replay is this product's headline claim**, which makes an overclaim
about it the most expensive wording defect available here.

Every conflation found in the neighbourhood, changed or not:

| where | said | verdict |
|---|---|---|
| `resource_link` description | "the published, content-addressed episode bundle — replay it offline with `trvs verify-episode`" | **wrong, fixed.** Now names the receipt, lists what it contains, and says the bundle is on the server's disk and is what `verify-episode` replays. |
| `describe()` instructions | "Every episode is published as a content-addressed bundle **readable at** `trvs://episode/<episode_id>` and replayable offline" | **wrong, fixed.** Two true facts welded into a false one: the episode *is* published as a bundle, and the URI *is* readable, but the URI does not read the bundle. Now separates publication from URI. |
| `submit_candidate` description | "a link to the published episode" | loose, tightened to "a link to the sealed receipt of the published episode" — the model reads this before it reads the link. |
| `episode_uri` output schema property | no description | gained one: "reads back the episode's sealed receipt, not its bundle". |
| resource template title + description | "Published episode receipt" / "the sealed EpisodeReceiptV1…" | **already correct.** Extended with one clause naming what is *not* served, since that is the question a reader arrives with. |
| module docstring §"adapter not a second kernel" | "no receipt builder, no `write_episode_bundle` call" | **correct as written** — it is naming two seams it deliberately does not cross, and they are genuinely two different things. |
| `_read_episode` docstring | "Only the receipt… The bundle also holds the task, the reward spec, the snapshot, the trace and the verifier evidence" | **correct as written**, and is where the distinction was already stated properly. |
| `README.md` MCP section | resources are "`trvs://episode/{episode_id}`… a different receipt would have a different name" | **correct as written**; one clause added to say the URI serves the receipt and not the bundle. |

M39 pins it in two halves, and neither is sufficient alone. **The bytes**: what
the URI returns is exactly `receipt.json`, proved by reading both off disk, and
none of the bundle's other members is addressable through the scheme. **The
words**: of the two artifact nouns, the description of that URI must name
`receipt` *first*, because the first artifact a description names is the one it is
about. That rule fails the old sentence and passes a sentence that says "the
receipt … the full bundle is on disk" — which is the distinction that had to
survive, since naming the bundle in order to disclaim it is exactly what an honest
description does. A substring ban on "bundle" would have failed the fix.

### 18.3 Should a bundle resource exist? — considered, not built

Offline replay *is* the headline claim, so "the client can only get the receipt"
is a real limitation, not a technicality. Recommending against it anyway:

**What it would cost.**

1. **A second reader of the bundle.** §14.5 already rules that serving reward
   specs and snapshots would make `mcp.py` "a second, partial implementation of
   `verify-episode`'s reader, which has an opinion about closure that a resource
   read must not quietly approximate". A bundle resource is that objection at full
   strength: `episode_bundle.read_episode_bundle` verifies closure, and a resource
   read that skipped verification would hand a client bytes the verifier would
   have rejected — under a URI whose whole promise is content addressing.
2. **It breaks M30.** `mcp.py` may not name `episode_bundle`. That constraint is
   the structural form of "this module translates, it does not re-implement", and
   loosening it for a convenience read is exactly the kind of erosion M30 exists
   to make expensive.
3. **A resource is one document.** MCP's `contents` is a list of URI-addressed
   blobs; a bundle is a *directory with a manifest and relative member paths*.
   Serving it means either N resources under `trvs://episode/{id}/{member}` — a
   namespace whose members are not independently content-addressed, so the long
   `ttlMs` stops being earned — or one inlined JSON blob, which is a re-encoding
   of the bundle that `verify-episode` cannot read and that nothing else in the
   stack produces. Both are new artifacts.
4. **Scope.** Bundles carry the trace and the verifier evidence. Episodes are
   already `cacheScope: private` for a weaker reason (a submitter's finding and
   patch); a bundle read hands over the full execution record, and the scoping
   question deserves an answer rather than an inherited default.

**What it would buy.** Only the case where the client and the server are on
different machines *and* the client wants to replay independently. On stdio the
client is the parent process and the bundle is on the same filesystem: the
`resource_link` already names the id, and `trvs verify-episode <output>/<id>` is
the supported path. The remote case is Streamable HTTP, which is deferred (§10) —
so the transport this feature would serve does not exist yet.

**If it is ever built**, the shape should be `trvs://bundle/{episode_id}` as a
distinct authority rather than a mode of `episode/`, it should re-verify closure
before serving (delegating, never re-implementing), and it should ship with
`archive-bundle`'s canonical archive rather than a bespoke JSON encoding — so that
what a client receives is byte-identical to what `verify-bundle` already admits.
That is a slice, not a wording fix, and it is recorded here as a decision to defer
rather than an oversight.

## 19. A second round of external review (2026-08-04)

Two findings against this transport (19.1, 19.2), and one class found in the
neighbourhood and fixed outside these memos (19.3).

### 19.1 A defensive `_settle` released an id it did not hold — M40

`_run_one` wrapped `_dispatch` and, on any exception out of it, settled the id a
second time. The comment said *"settling again is harmless and an id that is
never settled could never be reused."* The second half is right and load-bearing.
The first half is true **only while `count == 1`**, and `_admit` counts precisely
because it need not be.

The reachable sequence, all of it on the wrong side of a pipe from a client this
server cannot constrain:

1. a client reuses an id while the first request is still running — a JSON-RPC
   violation, and `_admit` therefore records `count == 2` rather than refusing,
   because two answers for one id are indistinguishable afterwards and the
   invariant worth keeping is *the id is released when the **last** holder
   settles*;
2. worker A finishes and settles: `2 → 1`, and the entry correctly stays, for B;
3. A's `_write` raises — a closed pipe, a full disk;
4. `_run_one` settles again, unconditionally: `1 → 0`, **entry deleted**.

Worker B is still running and its id is now unknown. Both halves of the
cancellation contract break, and neither is visible from A:

- a `notifications/cancelled` for B is **ignored**, because an unknown id is one
  the spec says a server ignores — and it is unknown only because A failed;
- B's own `_settle` finds no entry and returns `True`, since an untracked id is
  not a cancelled one, so **B writes an answer for a withdrawn request** — the
  one thing the spec forbids outright.

It needs a protocol violation *and* a write failure, which is why it had not been
seen. That is a statement about how often it fires, not about whether it is
correct: this server's whole cancellation design assumes the id map is exact, and
a release performed on behalf of a still-running holder makes it inexact.

**The fix is not "settle less often."** A dispatch that dies *before* the release
must still release, or the id is poisoned forever — the failure the counting was
introduced to prevent in the first place. So `_dispatch` appends to a one-shot
ledger the moment it releases, and `_run_one` settles only on an empty one:
exactly one release per holder, whichever way the dispatch ended. M40 drives the
specific interleaving through the lifecycle methods rather than spawning two
threads and hoping for it, and pins the two paths the fix must not have broken —
a single holder whose write fails still frees its id, and a dispatch that raises
before releasing still releases.

### 19.2 `PAGINATION_PARAM` claimed a property it did not have

The constant's comment read *"One place, so the refusal and the law that drives
it cannot disagree about which key is being checked."* M38 hardcoded the literal
`{"cursor": …}` in every request it sent, and `PAGINATION_PARAM` was not in
`mcp.__all__` — unlike `PAGINATED_METHODS` and `SUPPORTS_PAGINATION`, its two
siblings. So the two *could* disagree: renaming the constant would have changed
the key the server checks while every law kept passing against the old one.

Made true rather than removed, because the property is worth having. M38 now
builds every request through `M.PAGINATION_PARAM`, and asserts the returned
`data.param` against it. The value is still pinned to the specification, by
**exactly one literal**, in M38 — that literal is not a duplicate of the
constant, it is the other half of the claim: without it a rename would carry
every law along with it, and with it a rename fails while a *relocation* of the
key stays one edit. `PAGINATION_PARAM` is now exported.

### 19.3 Also found in the neighbourhood, and fixed outside this memo

The `except (ValueError, UnicodeDecodeError)` clause around `json.loads` that O31
closed in `ors_server._body` was found at five further sites —
`batch.load_candidate_set`, `bundle.read_manifest`, `verify_bundle`'s
`environment.json` read, `evalsplit.open_environment` and
`evalsplit._read_member`. `RecursionError` is a `RuntimeError`, so a *legal* JSON
document nested past the decoder's stack escaped all five as an untyped crash
(measured: 200 000 nested arrays is 400 kB of ordinary bytes). All five now
refuse with the code each module already declared; the laws are B31 and D41.

`mcp_server._decode` carries the same clause and is **not** fixed here. It is
recorded as open in §17 instead, because the other five all had a caller holding a
typed refusal to hand back, and this one sits on the reader thread where what
should happen to the loop is a design question rather than a clause to widen. It
is the same class: a client's line reaches `json.loads`, and `MAX_LINE_BYTES`
bounds length, not depth.

Five further sites of the same clause remain outside both memos and are recorded
here so the sweep's boundary is stated rather than implied: `pack._read_json`,
`substrates._read_json`, `comparison._read_receipt`, `cli._load_json` and
`episode_bundle._load_json`. Three of them (`comparison`, `cli`,
`episode_bundle`) name only `ValueError`, so they are narrower still. None was
examined in this round.
