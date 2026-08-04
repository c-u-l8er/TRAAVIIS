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

So each request is dispatched on its own daemon thread and only the **writes**
are serialized, by a lock held just long enough to put one complete line on the
stream. Interleaving two half-written responses would corrupt the framing, which
is the one thing this transport genuinely owns.

Threads are not bounded. On stdio the peer is the parent process, which already
has strictly more authority than any request it could send; a queue limit here
would defend against a client that does not need defending against and would
stall the reader, which must stay live to receive `notifications/cancelled`.

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
"""

import json
import sys
import threading

from . import mcp as _mcp

__all__ = [
    "MAX_LINE_BYTES",
    "McpStdioServer",
    "serve_stdio",
]

#: A single message is a small document (a finding and a diff). The cap exists so
#: one malformed stream cannot exhaust memory before a newline arrives; it is the
#: same 8 MiB ORS allows for a body, for the same reason and with the same slack.
MAX_LINE_BYTES = 8 * 1024 * 1024


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
        #: Request ids a `notifications/cancelled` arrived for. Consulted just
        #: before a response is written, which is the last moment at which
        #: suppressing it is still possible.
        self._cancelled = set()
        self._cancelled_lock = threading.Lock()

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

    # --- dispatch ----------------------------------------------------------
    def handle_line(self, raw):
        """Decode, dispatch and write one line. Returns the response, or None.

        Returning the response as well as writing it is what lets the battery
        assert on semantics without re-parsing the stream, while still exercising
        the real framing path.
        """
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            # A parse failure has no id to correlate against — JSON-RPC says the
            # id is null in exactly this case, and guessing one would attach an
            # error to a request that may not exist.
            response = _mcp._error_response(
                None, _mcp.ERR_PARSE, "Parse error: %s" % exc)
            self._write(response)
            return response

        if isinstance(message, dict) \
                and message.get("method") == "notifications/cancelled":
            self._cancel(message)
            return None

        try:
            response = self.adapter.handle(message)
        except Exception as exc:  # pragma: no cover - defensive
            # An unexpected exception must not take the reader thread down with
            # it, and must not leave the client waiting on an id forever. It is
            # reported as an internal error against that id, which is true.
            ident = message.get("id") if isinstance(message, dict) else None
            if ident is None:
                self._note("mcp: notification raised %s: %s\n"
                           % (type(exc).__name__, exc))
                return None
            response = _mcp._error_response(
                ident, _mcp.ERR_INTERNAL,
                "internal error: %s: %s" % (type(exc).__name__, exc))

        if response is None:
            return None
        if self._is_cancelled(response.get("id")):
            # The client withdrew this request. The spec forbids any further
            # message for it, so the answer is dropped — see the module
            # docstring for why the *work* was not.
            self._note("mcp: response for cancelled request %r suppressed\n"
                       % (response.get("id"),))
            return None
        self._write(response)
        return response

    def _cancel(self, message):
        params = message.get("params")
        ident = params.get("requestId") if isinstance(params, dict) else None
        if ident is None:
            return
        with self._cancelled_lock:
            self._cancelled.add(_hashable(ident))

    def _is_cancelled(self, ident):
        with self._cancelled_lock:
            return _hashable(ident) in self._cancelled

    # --- the loop ----------------------------------------------------------
    def serve_forever(self):
        """Read until EOF, dispatching each message on its own thread.

        EOF on stdin is the portable graceful-shutdown signal and the only one
        the spec asks a stdio server to honour, so it is the loop's exit
        condition rather than a signal handler.
        """
        stream = self.stdin
        while True:
            line = stream.readline(MAX_LINE_BYTES)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            thread = threading.Thread(target=self._run_one, args=(line,),
                                      daemon=True)
            with self._threads_lock:
                self._threads.append(thread)
            thread.start()
        self.drain()

    def _run_one(self, line):
        try:
            self.handle_line(line)
        except Exception as exc:  # pragma: no cover - defensive
            self._note("mcp: dispatch failed: %s: %s\n"
                       % (type(exc).__name__, exc))

    def drain(self, timeout=30):
        """Wait for in-flight requests after EOF.

        An episode in flight when the client closed the pipe is still going to be
        published — `write_episode_bundle` stages, re-verifies, fsyncs and
        renames — and exiting out from under it would abandon a scored episode
        somewhere in that sequence. The client is gone and will never read the
        answer; the evidence is the part worth waiting for.
        """
        with self._threads_lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=timeout)


def _hashable(ident):
    """JSON-RPC ids are strings or numbers; normalise for set membership."""
    if isinstance(ident, bool):  # bool is an int subclass; ids are not bools
        return ("bool", ident)
    if isinstance(ident, (int, float)):
        return ("num", float(ident))
    return ("str", ident)


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
