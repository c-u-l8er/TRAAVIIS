"""The stdio transport for `Residency MCP Profile v1` (MCP revision `2026-07-28`).

A thin framing layer over `mcp.McpAdapterV1`. Everything with a decision in it —
the tool catalog, the resource space, the error sorting, the version check —
lives in `mcp`; this module reads newline-delimited JSON off one stream, hands
each message to the adapter, and writes the answer to another. That split is what
lets the M-battery drive the whole protocol without a pipe or a subprocess, the
same way the O-battery drives ORS without a socket.

No dependency is added. `json`, `sys`, `threading` — standard library, like the
kernel and like the ORS transport. A benchmark server that has to install a
protocol SDK to accept a submission is a server people run a modified copy of.

Why stdio, and only stdio
-------------------------

ORS chose loopback with a deliberately blunt `--allow-remote`, because an HTTP
server that holds a candidate's patches and runs verifier commands should not be
reachable by accident. Stdio is strictly better on that axis: there is **no
network surface at all**. The client is the process that launched this one, the
channel is a pipe it already owns, and there is no port, no bind address, no
origin check and no flag that could expose one. `--allow-remote` has no meaning
here and deliberately does not exist.

Streamable HTTP is **not implemented**, and that is a decision rather than an
omission. Under `2026-07-28` a conforming Streamable HTTP server owes standard
request headers (`Mcp-Method`, `Mcp-Name`, `MCP-Protocol-Version`), origin
validation, the authorization framework, `x-mcp-header` parameter mirroring, and
`subscriptions/listen` as a long-lived POST-response notification stream. A
partial implementation of that would advertise a transport this server does not
actually speak — the same species of false claim as a tool catalog listing an
operation the substrate refuses, which is the rule ORS §2 set. The kernel already
has an HTTP surface, `trvs serve --ors`; a client that wants one should use it.

Concurrency: the reason there is a thread here at all
-----------------------------------------------------

The obvious stdio server is a loop: read a line, handle it, write the answer,
read the next. It is also wrong for this kernel, and wrong in a way that is
invisible until it matters.

`2026-07-28` states that clients may interleave unrelated requests on one
transport, and that an open stdio process "is not a conversation or session". The
Finalize Linearization Closure (K25, K26) established that two *different*
sessions must be able to be inside the scoring work at the same time, and proved
it with a barrier that would deadlock a kernel which serialized finalization. A
read-handle-write loop reintroduces exactly that serialization one layer up: the
kernel would still be linearizable and the server would still score one episode
at a time, so the property would be preserved on paper and lost in fact.

So each request is dispatched on its own thread and only the **writes** are
serialized, by a lock held just long enough to put one complete line on the
stream. Interleaving two half-written responses would corrupt the framing, which
is the one thing this transport genuinely owns.

Threads are not bounded. On stdio the peer is the parent process, which already
has strictly more authority than any request it could send; a queue limit here
would defend against a client that does not need defending against and would
stall the reader, which must stay live to receive `notifications/cancelled`.

The request lifecycle, and why it is a state machine
----------------------------------------------------

A request id passes through exactly three states here, and the third is the same
as the first:

    unknown ──admit──▶ running ──settle──▶ unknown
                          │                   ▲
                       cancel                 │
                          ▼                   │
                      cancelled ───settle─────┘

**unknown** is where every id starts and where every id ends. An id this server
does not currently own is an id it will not act on: a `notifications/cancelled`
naming one is ignored, which is what the spec permits ("the referenced request is
unknown", "processing has already completed") and what stops a cancellation from
outliving the request it named.

The first version of this transport had no such map. It recorded every cancelled
id in a permanent set and consulted it just before writing, which made three
different mistakes at once and only the third was visible. It accepted a
cancellation for a request that did not exist; it never released an id when the
request finished; and, because JSON-RPC ids are chosen by the client and are
routinely small integers, a cancellation for id `7` sent before any request `7`
existed would silently suppress the response to *every* later request numbered
`7`. The work ran and the answer was thrown away, and the client waited forever
for an answer this server had already decided not to send. A cancellation is a
statement about one request in progress; a permanent set turns it into a
statement about a number.

**Admission happens on the reader thread, in stream order**, before the work is
handed to a worker. That ordering is load-bearing rather than incidental. A
client that cancels its own request sent the request first, so the request is
read first; admitting it there means the id is already `running` by the time the
cancellation is considered. Admit on the worker instead and the cancellation
races the thread start, is found to be unknown, and is ignored — a permitted
answer, arrived at by accident, to a question that was not asked.
`notifications/cancelled` is likewise handled *on the reader*, not dispatched to
a thread that could overtake or be overtaken by the request it refers to.

Cancellation, and what this server will not claim about it
----------------------------------------------------------

`notifications/cancelled` is honoured to the letter of what is achievable and no
further. The spec requires that a server send no further messages for a cancelled
request; it says work SHOULD stop "as soon as practical". This server **suppresses
the response and does not stop the work**, because it cannot: the kernel has no
cancel, `finalize` is a one-shot claim, and interrupting a verifier plan
mid-flight would leave the session `finalizing` forever — un-closeable by
`KERNEL_SESSION_BUSY` and un-scorable by anyone. Killing the work to look
responsive would trade a slow answer for a permanently wedged session.

The episode is therefore still scored and still published. That is recoverable
rather than lost: the evidence is on disk before `finished` is ever reported, and
the next `submit_candidate` on that handle is refused with the `episode_id` and a
`trvs://episode/…` link attached. A cancelled submission loses its *report*, not
its *result*.

### The race the spec names, and where this server puts the line

"Due to network latency, cancellation notifications may arrive after request
processing has completed, and potentially after a response has already been
sent." Both parties MUST handle that gracefully, and the spec then permits either
outcome: a server SHOULD not send a response for a cancelled request (§3) and MAY
ignore a cancellation whose request has already completed (§4), while the client
SHOULD ignore a late response either way (§5).

Permitting both outcomes is not the same as being allowed to produce neither, or
both. So this transport names **one** commit point and makes it atomic:
`_settle` and `_cancel` contend for the same lock, and whichever takes it first
decides.

- cancel wins the lock → the response is suppressed, and the id is released when
  the request settles;
- settle wins the lock → the response is written, the id is released in the same
  critical section, and the cancellation that arrives afterwards finds an unknown
  id and is ignored.

The commit point is the **write decision**, not the completion of the work: the
work completing is not a moment this transport can observe (it deliberately owns
no part of the scoring), whereas the decision to write is a line of code here.
So a cancellation that lands while the answer is computed but not yet written is
*honoured* — that is the resolution, chosen because it is the one the transport
can implement without reaching into the adapter, and because it is the branch the
spec states as a SHOULD rather than a MAY.

What is guaranteed in every interleaving, and is what the laws actually check, is
narrower and stronger than "cancel wins" or "completion wins": the response is
never both written and suppressed, never neither, and **the id never survives its
request**. A later request may reuse it freely.

Shutdown: EOF, and why a longer timeout was the wrong repair
-------------------------------------------------------------

EOF on stdin is the portable graceful-shutdown signal and the only one the spec
asks a stdio server to honour, so it is the read loop's exit condition rather
than a signal handler.

What happens next used to contradict itself. `drain()` argued — correctly — that
an episode in flight when the client closed the pipe is still going to be
published, that `write_episode_bundle` stages, re-verifies, fsyncs and renames,
and that "the evidence is the part worth waiting for". It then joined each worker
with a 30-second deadline and returned regardless, and the workers were daemon
threads, so the process exited and killed them. A verifier plan that legitimately
takes longer than thirty seconds — which is most of them, on a real repository —
was abandoned mid-publication by the *normal* shutdown path, having already spent
the candidate's one-shot session.

Raising the number does not fix that, it relocates it: whatever constant is
chosen, the transport is asserting a bound on how long a verifier plan may
honestly take, and it has no basis for one. So two changes, and neither is a
number:

1. **Workers are not daemon threads.** This is the load-bearing half. A daemon
   thread is one the interpreter is permitted to kill at exit; making the workers
   ordinary threads means the process *cannot* exit out from under a publication,
   whatever `drain` does, whatever raises, and whoever calls `serve_forever`.
   Durability stops depending on a wait completing and starts depending on the
   interpreter's own shutdown, which joins non-daemon threads unconditionally.
   All workers are non-daemon, not just publishing ones: classifying requests by
   whether they publish would put a list in this module that has to be kept in
   step with a catalog it does not own, and a stale classification fails silently
   in the direction of abandonment.

2. **`drain()` has no deadline by default.** `timeout=None` is not a large
   number, it is the absence of one: EOF means the client is gone, not that the
   evidence stopped mattering. A caller may still pass a finite timeout — an
   operator supervising a shutdown reasonably wants to be told about a wait that
   is not ending — but it is a *reporting* knob and not an abandonment knob. It
   makes `drain` stop blocking; it does not make the process exit, because of
   (1), and it returns the unfinished requests by name and says so on stderr
   rather than returning as though nothing were outstanding.

The remaining cost is stated rather than hidden: a genuinely hung verifier now
hangs the shutdown instead of silently discarding a scored episode. That trade is
deliberate. A hang is visible, is attributable to the request named on stderr,
and leaves the bundle in the staged-or-renamed state `write_episode_bundle`
already guarantees; a silent abandonment is invisible, and costs the candidate a
chance that does not come back.
"""

import json
import sys
import threading

from . import mcp as _mcp

__all__ = [
    "CANCELLED",
    "MAX_LINE_BYTES",
    "RUNNING",
    "McpStdioServer",
    "serve_stdio",
]

#: A single message is a small document (a finding and a diff). The cap exists so
#: one malformed stream cannot exhaust memory before a newline arrives; it is the
#: same 8 MiB ORS allows for a body, for the same reason and with the same slack.
MAX_LINE_BYTES = 8 * 1024 * 1024

#: The two states an id can be in while this server owns it. An id in neither is
#: **unknown** — the state every id starts in and returns to — and a cancellation
#: naming an unknown id is ignored, which is what keeps a cancellation a statement
#: about a request rather than about a number.
RUNNING = "running"
CANCELLED = "cancelled"


class McpStdioServer(object):
    """Serve one `McpAdapterV1` over a pair of byte streams.

    Streams are injected rather than reached for. `sys.stdin.buffer` is the
    default in practice, but the battery drives this with pipes, and a transport
    that could only be tested by spawning a subprocess is a transport whose
    framing is tested by inspection.
    """

    def __init__(self, adapter, *, stdin=None, stdout=None, log=None):
        self.adapter = adapter
        self._stdin = stdin
        self._stdout = stdout
        self._log = log
        self._write_lock = threading.Lock()
        self._threads = []
        self._threads_lock = threading.Lock()
        #: The request ids this server currently owns: key → `{"state", "count"}`.
        #: An id that is absent is *unknown*, and a cancellation for an unknown id
        #: is ignored. The map is the whole reason a cancellation cannot outlive
        #: the request it named — see the module docstring.
        self._inflight = {}
        self._inflight_lock = threading.Lock()
        #: Set the moment `drain` begins waiting. A shutdown that is in progress
        #: is observable rather than inferred from a sleep, which is what lets a
        #: law assert "the drain is waiting" instead of guessing that it is.
        self.draining = threading.Event()

    # --- streams -----------------------------------------------------------
    @property
    def stdin(self):
        return self._stdin if self._stdin is not None else sys.stdin.buffer

    @property
    def stdout(self):
        return self._stdout if self._stdout is not None else sys.stdout.buffer

    def _note(self, text):  # pragma: no cover - I/O only
        """Log to stderr. Never to stdout — stdout carries MCP messages only.

        The spec is blunt about this: the server MUST NOT write anything to
        stdout that is not a valid MCP message. A stray `print` would not look
        like a bug; it would look to the client like a protocol violation from a
        server it otherwise trusts.
        """
        if self._log is not None:
            self._log(text)

    def _write(self, message):
        """Write one message as one line, atomically with respect to other writers.

        `json.dumps` without `indent` cannot emit a literal newline — control
        characters inside strings are escaped — so "one message per line, no
        embedded newlines" is a property of the encoder rather than something
        this method has to police afterwards.
        """
        line = json.dumps(message, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        assert b"\n" not in line, "a framed message may not contain a newline"
        with self._write_lock:
            stream = self.stdout
            stream.write(line + b"\n")
            stream.flush()

    # --- the request lifecycle ---------------------------------------------
    def in_flight(self):
        """A snapshot of the ids this server currently owns: key → state.

        Read-only and copied, so that a caller inspecting the lifecycle cannot
        become a second writer of it.
        """
        with self._inflight_lock:
            return dict((key, entry["state"])
                        for key, entry in self._inflight.items())

    def _admit(self, ident):
        """unknown → running. Returns the key to settle with, or `None`.

        `None` means "not an id this server will track": a notification, or an
        id of a shape JSON-RPC does not permit. Such a request is answered
        normally and is simply not cancellable, because there is nothing a
        cancellation could unambiguously name.
        """
        key = _request_key(ident)
        if key is None:
            return None
        with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                self._inflight[key] = {"state": RUNNING, "count": 1}
            else:
                # A client that reuses an id while the first request is still
                # running is violating JSON-RPC, and this server could not tell
                # the two responses apart afterwards either. Counting keeps the
                # invariant that actually matters: the id is released when the
                # last holder settles — never earlier, which would leave the
                # survivor uncancellable, and never later, which is the poisoning
                # this map exists to prevent.
                entry["count"] += 1
        return key

    def _cancel(self, message):
        """running → cancelled, and nothing else. Returns whether it was taken.

        Every refusal here is the spec's own: a malformed notification, an
        unknown request id and an already-completed request are all listed as
        cancellations a server ignores. Ignoring is not laxity — it is the only
        answer that keeps the notification a statement about a request in
        progress.
        """
        params = message.get("params")
        ident = params.get("requestId") if isinstance(params, dict) else None
        key = _request_key(ident)
        if key is None:
            self._note("mcp: ignoring a cancellation whose requestId is not a "
                       "JSON-RPC request id: %r\n" % (ident,))
            return False
        with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                # Unknown, or already answered. Recording it would make this
                # server refuse to answer some *later* request that happens to
                # reuse the number.
                self._note("mcp: ignoring a cancellation for %r, which is not "
                           "in flight\n" % (ident,))
                return False
            entry["state"] = CANCELLED
        return True

    def _settle(self, key):
        """running | cancelled → unknown. True if the response may still be written.

        The commit point. This and `_cancel` contend for one lock, so a
        cancellation racing a completion produces exactly one of the two outcomes
        the spec permits, and in both of them the id is released.
        """
        if key is None:
            return True
        with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                # Not tracked, or already settled by a defensive second call.
                return True
            entry["count"] -= 1
            if entry["count"] <= 0:
                del self._inflight[key]
            return entry["state"] != CANCELLED

    # --- dispatch ----------------------------------------------------------
    def _decode(self, raw):
        """Returns `(message, error_response)`; exactly one of them is not None."""
        try:
            return json.loads(raw.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError) as exc:
            # A parse failure has no id to correlate against — JSON-RPC says the
            # id is null in exactly this case, and guessing one would attach an
            # error to a request that may not exist.
            return None, _mcp._error_response(
                None, _mcp.ERR_PARSE, "Parse error: %s" % exc)

    def handle_line(self, raw):
        """Decode, dispatch and write one line. Returns the response, or None.

        Returning the response as well as writing it is what lets the battery
        assert on semantics without re-parsing the stream, while still exercising
        the real framing path.
        """
        message, failed = self._decode(raw)
        if failed is not None:
            self._write(failed)
            return failed
        return self.handle_message(message)

    def handle_message(self, message):
        """Admit, dispatch, settle: one already-parsed message's whole lifecycle."""
        if _is_cancellation(message):
            self._cancel(message)
            return None
        return self._dispatch(message, self._admit(_ident_of(message)))

    def _dispatch(self, message, key, settled=None):
        """Run the adapter and decide, once, whether the answer goes on the wire.

        `settled` is an optional one-shot ledger: a list this call appends to the
        moment it releases `key`. It exists so a caller that has to settle
        defensively can tell whether the release has already happened. See
        `_run_one`, which is the only caller that passes one and the only place
        the difference is observable.
        """
        def settle():
            if settled is not None:
                settled.append(key)
            return self._settle(key)

        try:
            response = self.adapter.handle(message)
        except Exception as exc:  # pragma: no cover - defensive
            # An unexpected exception must not take the reader thread down with
            # it, and must not leave the client waiting on an id forever. It is
            # reported as an internal error against that id, which is true.
            ident = _ident_of(message)
            if ident is None:
                self._note("mcp: notification raised %s: %s\n"
                           % (type(exc).__name__, exc))
                settle()
                return None
            response = _mcp._error_response(
                ident, _mcp.ERR_INTERNAL,
                "internal error: %s: %s" % (type(exc).__name__, exc))

        if response is None:
            settle()
            return None
        if not settle():
            # The client withdrew this request. The spec forbids any further
            # message for it, so the answer is dropped — see the module
            # docstring for why the *work* was not.
            self._note("mcp: response for cancelled request %r suppressed\n"
                       % (response.get("id"),))
            return None
        self._write(response)
        return response

    # --- the loop ----------------------------------------------------------
    def serve_forever(self):
        """Read until EOF, dispatching each request on its own thread.

        Parsing, cancellation and admission all happen here, on the reader, in
        the order the client sent them. Only the *work* goes to a thread. A
        cancellation dispatched to a thread of its own could overtake the request
        it names or be overtaken by it, and both orders would be decided by the
        scheduler rather than by the stream.
        """
        stream = self.stdin
        try:
            while True:
                line = stream.readline(MAX_LINE_BYTES)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                message, failed = self._decode(line)
                if failed is not None:
                    self._write(failed)
                    continue
                if _is_cancellation(message):
                    self._cancel(message)
                    continue
                ident = _ident_of(message)
                key = self._admit(ident)
                thread = threading.Thread(
                    target=self._run_one, args=(message, key),
                    name=_thread_name(message), daemon=False)
                with self._threads_lock:
                    self._threads.append(thread)
                thread.start()
        finally:
            # In a `finally` because a reader that dies on a stream error owes
            # the in-flight publications the same wait a clean EOF does.
            self.drain()

    def _run_one(self, message, key):
        settled = []
        try:
            self._dispatch(message, key, settled)
        except Exception as exc:  # pragma: no cover - defensive
            # `_dispatch` settles before it writes, so an exception out of it is
            # normally a failure of the write. The id must still be released --
            # one that is never settled could never be reused -- but only if this
            # dispatch has not released it already.
            #
            # "Settling again is harmless" was the previous claim, and it holds
            # only while `count == 1`. `_admit` counts, because a client that
            # reuses an in-flight id (a JSON-RPC violation, but one this server
            # is on the wrong side of a pipe from) gets `count == 2` and two
            # workers on one key. Then: worker A settles (2 -> 1, entry stays,
            # correctly, for worker B), A's `_write` raises, and an unconditional
            # second settle here takes 1 -> 0 and *deletes the entry* -- a
            # release performed on behalf of a holder that is still running. B's
            # own `_settle` then finds no entry, returns `True` because an
            # untracked id is not a cancelled one, and writes a response for a
            # request the client had cancelled. The count is the invariant, and
            # decrementing it twice for one holder breaks it.
            #
            # So the second settle is conditional on the ledger `_dispatch`
            # appends to. It stays *unconditionally correct* rather than merely
            # rarer: if the exception came from before the release (building the
            # error response, `_ident_of`), the ledger is empty and the id is
            # released here as it always was.
            if not settled:
                self._settle(key)
            self._note("mcp: dispatch failed: %s: %s\n"
                       % (type(exc).__name__, exc))
        finally:
            self._retire(threading.current_thread())

    def _retire(self, thread):
        """Drop a finished worker, so `_threads` means "still running"."""
        with self._threads_lock:
            try:
                self._threads.remove(thread)
            except ValueError:  # pragma: no cover - defensive
                pass

    def drain(self, timeout=None):
        """Wait for in-flight requests after EOF. Returns the ones that outlasted
        `timeout`, which by default there is not one of.

        An episode in flight when the client closed the pipe is still going to be
        published — `write_episode_bundle` stages, re-verifies, fsyncs and
        renames — and exiting out from under it would abandon a scored episode
        somewhere in that sequence. The client is gone and will never read the
        answer; the evidence is the part worth waiting for.

        `timeout=None` is the default because any constant would be this module
        asserting a bound on how long a verifier plan may honestly take, which it
        has no basis for. A caller supervising a shutdown may still pass one; it
        stops `drain` blocking and reports what is outstanding, and it does not
        abandon anything, because the workers are not daemon threads and the
        process will not exit until they finish.
        """
        self.draining.set()
        with self._threads_lock:
            threads = list(self._threads)
        unfinished = []
        for thread in threads:
            thread.join(timeout=timeout)
            if thread.is_alive():
                unfinished.append(thread.name)
        if unfinished:
            self._note(
                "mcp: drain stopped waiting after %ss with %d request(s) still "
                "running: %s. They are not daemon threads, so this process will "
                "not exit until they finish.\n"
                % (timeout, len(unfinished), ", ".join(unfinished)))
        return unfinished


def _is_cancellation(message):
    return (isinstance(message, dict)
            and message.get("method") == "notifications/cancelled")


def _ident_of(message):
    return message.get("id") if isinstance(message, dict) else None


def _request_key(ident):
    """The tracking key for a JSON-RPC request id, or `None` if it is not one.

    JSON-RPC 2.0 permits a String or a Number, and this MCP revision forbids the
    Null the base protocol merely discourages. Nothing else is a request id: an
    object, an array or a boolean in `requestId` is a malformed notification,
    which the spec says to ignore. Validating here rather than trusting the field
    also keeps an unhashable value from reaching a `set` lookup, where it would
    become an exception in a code path whose entire job is not to have any.
    """
    if ident is None or isinstance(ident, bool):
        return None
    if isinstance(ident, (int, float, str)):
        return _hashable(ident)
    return None


def _hashable(ident):
    """JSON-RPC ids are strings or numbers; normalise for map membership."""
    if isinstance(ident, bool):  # bool is an int subclass; ids are not bools
        return ("bool", ident)
    if isinstance(ident, (int, float)):
        return ("num", float(ident))
    return ("str", ident)


def _thread_name(message):
    """Name a worker after the request it is running, so `drain` can name it.

    Both halves are client-supplied and both are truncated. A line cap of 8 MiB
    is a cap on a *message*, not on a thread name that ends up in a log a person
    reads during a shutdown that is not ending.
    """
    method = message.get("method") if isinstance(message, dict) else None
    return "mcp-request id=%.64s method=%.64s" % (repr(_ident_of(message)),
                                                  repr(method))


def serve_stdio(package, split, output, *, engine=None, registry=None,
                platform="unknown", toolchain=None, on_wiring_notes=None,
                log=None, stdin=None, stdout=None):
    """Admit everything, **then** read the first message.

    The same ordering property `trvs serve --ors` has, and for the same reason,
    with the socket removed. `mcp.open_adapter` either returns a fully admitted
    environment or raises; only after it returns does this server look at its
    input. There is no arrangement of failures that produces a process answering
    `tools/list` over a package that did not admit — which matters more on stdio
    than over HTTP, because the client here launched the process and will read
    "it started" as "it is serving".
    """
    adapter = _mcp.open_adapter(
        package, split, output, engine=engine, registry=registry,
        platform=platform, toolchain=toolchain,
        on_wiring_notes=on_wiring_notes)
    return McpStdioServer(adapter, stdin=stdin, stdout=stdout, log=log)
