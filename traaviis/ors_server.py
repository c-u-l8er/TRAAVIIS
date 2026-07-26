"""The HTTP transport for `Residency Submission ORS Profile v1`.

A thin encoding of `ors.OrsAdapterV1` over the standard library's
`ThreadingHTTPServer`. Everything with a decision in it lives in `ors`; this
module parses a request line, decodes a JSON body, calls one adapter method,
and encodes the answer. That split is what lets the O-battery drive the entire
protocol without opening a socket — the tests exercise the semantics rather than
a client library.

No dependency is added. The kernel and the adapter are standard-library Python
and so is this, which matters more than usual for a server: a benchmark harness
that has to install a web framework to accept a submission is a harness people
will run a modified copy of.

Concurrency
-----------

`ThreadingHTTPServer` means two submissions really can be in flight at once.
That is not incidental — it is the condition the Finalize Linearization Closure
was built for, and it is why this transport is safe to write. Two threads
submitting to the *same* session race into `kernel.finalize`, exactly one takes
the claim, and the loser is refused by name. Two threads submitting to
*different* sessions proceed in parallel, because no lock is held across the
scoring work at either layer.

Binding
-------

The default bind address is loopback. A benchmark server holds a candidate's
patches and a verifier that runs test commands, and the default for a thing like
that is not "reachable from the network". `--allow-remote` is required for any
non-loopback host, and it is a flag rather than an inference from the address so
that exposing the server is something a human typed.
"""

import json
import re
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ors as _ors
from . import kernel as _kernel
from . import substrates as _substrates

__all__ = [
    "ORS_BASE_PATH",
    "IDEMPOTENCY_HEADER",
    "LOOPBACK_HOSTS",
    "status_for",
    "make_handler",
    "OrsHttpServer",
    "serve",
]

#: Every route lives under one versioned prefix, so a future profile version is
#: a sibling rather than a breaking change to these paths.
ORS_BASE_PATH = "/ors/v1"

#: Idempotency is a **transport** concern, so the key is a header. Putting it in
#: the payload would make it part of the submission document — a field the
#: client supplies that affects server behaviour, which is the exact category
#: `RemoteSubmissionV1` exists to keep empty.
IDEMPOTENCY_HEADER = "Idempotency-Key"

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

#: Bodies are small documents (a finding and a diff). A cap exists so that a
#: single client cannot exhaust the server's memory with one request; it is
#: generous enough that a real patch is never near it.
MAX_BODY_BYTES = 8 * 1024 * 1024

_SESSION_RE = re.compile(r"^/sessions/([^/]+)(?:/([a-z_]+))?$")
_TASK_RE = re.compile(r"^/tasks/([^/]+)$")

#: How a typed refusal becomes an HTTP status.
#:
#: The mapping is stated as data rather than derived from the code prefix
#: because the two are not the same question: `ORS_SESSION_FINISHED` and
#: `ORS_SUBMISSION_FIELD` are both client-caused, but one is a conflict with
#: state the client already created and the other is a malformed document, and a
#: client retrying the two should behave differently.
_STATUS = {
    "ORS_SESSION_UNKNOWN": 404,
    "ORS_TOOL_UNKNOWN": 404,
    "KERNEL_TASK_UNKNOWN": 404,
    "ORS_SESSION_FINISHED": 409,
    "ORS_SESSION_BUSY": 409,
    "KERNEL_SESSION_STATE": 409,
    "KERNEL_SESSION_BUSY": 409,
    "KERNEL_OPERATION_UNSUPPORTED": 501,
    "ORS_PUBLISH_FAILED": 500,
    "ORS_NO_EVIDENCE": 500,
}


def status_for(code):
    """The HTTP status for a typed refusal code. 400 for anything unlisted.

    400 is the default because the overwhelming majority of typed refusals in
    this codebase describe a document the caller sent, and a refusal whose
    status was never considered should read as "your request was wrong" rather
    than as "the server broke" — the second invites a retry that cannot help.
    """
    return _STATUS.get(code, 400)


def make_handler(adapter, *, log=None):
    """Build a request-handler class bound to one adapter.

    A class per server rather than a module-level handler reading a global: two
    adapters in one process (which the battery does) must not be able to see
    each other's sessions.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "traaviis-ors/1"
        protocol_version = "HTTP/1.1"
        _adapter = adapter

        # --- plumbing --------------------------------------------------
        def log_message(self, fmt, *args):  # pragma: no cover - I/O only
            if log is not None:
                log("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status, payload):
            body = (json.dumps(payload, indent=2, sort_keys=True,
                               ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The client hung up. The episode is already published — that
                # happened before this response was assembled — so losing the
                # response loses a *report*, not a result. The client's
                # idempotency key retrieves it.
                self.close_connection = True

        def _refuse(self, exc):
            code = getattr(exc, "code", None) or "ORS_ERROR"
            self._send(status_for(code), {
                "error": {"code": code,
                          "message": getattr(exc, "message", None) or str(exc),
                          "detail": getattr(exc, "detail", None) or {}},
            })

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise _ors.OrsError("ORS_BAD_REQUEST", "malformed Content-Length")
            if length > MAX_BODY_BYTES:
                raise _ors.OrsError(
                    "ORS_BODY_TOO_LARGE",
                    "request body exceeds %d bytes" % MAX_BODY_BYTES)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise _ors.OrsError("ORS_BAD_REQUEST",
                                    "request body is not valid JSON: %s" % exc)

        def _route(self):
            path = self.path.split("?", 1)[0]
            if not path.startswith(ORS_BASE_PATH):
                return None
            return path[len(ORS_BASE_PATH):] or "/"

        # --- verbs -----------------------------------------------------
        def do_GET(self):
            rest = self._route()
            if rest is None:
                return self._send(404, {"error": {
                    "code": "ORS_NOT_FOUND",
                    "message": "no such path; the ORS profile lives under %s"
                               % ORS_BASE_PATH, "detail": {}}})
            try:
                if rest in ("/", "/describe"):
                    return self._send(200, self._adapter.describe())
                if rest == "/tasks":
                    return self._send(200, {"tasks": self._adapter.list_tasks()})
                match = _TASK_RE.match(rest)
                if match:
                    return self._send(200, self._adapter.task(match.group(1)))
            except _substrates.AdmissionError as exc:
                return self._refuse(exc)
            return self._send(404, {"error": {"code": "ORS_NOT_FOUND",
                                              "message": "no such path: %s" % rest,
                                              "detail": {}}})

        def do_POST(self):
            rest = self._route()
            if rest is None:
                return self._send(404, {"error": {
                    "code": "ORS_NOT_FOUND",
                    "message": "no such path; the ORS profile lives under %s"
                               % ORS_BASE_PATH, "detail": {}}})
            try:
                body = self._body()
                if rest == "/sessions":
                    task_id = body.get("task_id")
                    if not isinstance(task_id, str) or not task_id:
                        raise _ors.OrsError(
                            "ORS_BAD_REQUEST",
                            "opening a session needs a task_id")
                    return self._send(201, self._adapter.open_session(task_id))
                match = _SESSION_RE.match(rest)
                if match:
                    session_id, action = match.group(1), match.group(2)
                    if action == "call_tool":
                        key = self.headers.get(IDEMPOTENCY_HEADER)
                        out = self._adapter.call_tool(
                            session_id, body.get("tool"),
                            body.get("arguments"), idempotency_key=key)
                        return self._send(200, out)
                    if action in _kernel.INTERACTIVE_OPERATIONS:
                        # Present, and refusing in the substrate's own words.
                        return self._adapter.unsupported(action)
            except _substrates.AdmissionError as exc:
                return self._refuse(exc)
            return self._send(404, {"error": {"code": "ORS_NOT_FOUND",
                                              "message": "no such path: %s" % rest,
                                              "detail": {}}})

        def do_DELETE(self):
            rest = self._route()
            if rest is None:
                return self._send(404, {"error": {
                    "code": "ORS_NOT_FOUND", "message": "no such path",
                    "detail": {}}})
            match = _SESSION_RE.match(rest or "")
            if match and match.group(2) is None:
                try:
                    return self._send(
                        200, self._adapter.close_session(match.group(1)))
                except _substrates.AdmissionError as exc:
                    return self._refuse(exc)
            return self._send(404, {"error": {"code": "ORS_NOT_FOUND",
                                              "message": "no such path: %s" % rest,
                                              "detail": {}}})

    return Handler


class OrsHttpServer(object):
    """A bound, not-yet-serving ORS server.

    Construction binds; `serve_forever` serves. The two are separate because the
    caller has to be able to *report* the bound port — a `--port 0` server is a
    genuinely useful thing (the battery uses it) and a caller cannot print the
    port it got if binding and serving are one call that never returns.
    """

    def __init__(self, adapter, host="127.0.0.1", port=8080, *,
                 allow_remote=False, log=None):
        if host not in LOOPBACK_HOSTS and not allow_remote:
            raise _ors.OrsError(
                "ORS_REMOTE_BIND_REFUSED",
                "refusing to bind %r: this server holds a candidate's patches "
                "and runs verifier commands, so exposing it beyond loopback is "
                "an explicit act — pass --allow-remote if that is what you mean"
                % host,
                {"host": host, "loopback": list(LOOPBACK_HOSTS)})
        self.adapter = adapter
        self._httpd = ThreadingHTTPServer(
            (host, port), make_handler(adapter, log=log))
        self._httpd.daemon_threads = True
        self._thread = None
        self._serving = threading.Event()

    @property
    def host(self):
        return self._httpd.server_address[0]

    @property
    def port(self):
        return self._httpd.server_address[1]

    @property
    def base_url(self):
        host = self.host
        if ":" in host:  # IPv6 literal
            host = "[%s]" % host
        return "http://%s:%d%s" % (host, self.port, ORS_BASE_PATH)

    def serve_forever(self):
        self._serving.set()
        try:
            self._httpd.serve_forever(poll_interval=0.2)
        finally:
            self._serving.clear()

    def start_background(self):
        """Serve on a daemon thread. Returns self, once it is actually serving."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        self._serving.wait(timeout=5)
        return self

    def shutdown(self):
        """Stop serving if serving, and release the socket either way.

        `BaseServer.shutdown` waits for `serve_forever` to acknowledge, so it
        blocks *forever* on a server that was bound but never served. Binding
        and serving are separate here by design, so releasing a bound-only
        socket has to be possible — otherwise anything that fails between the
        two (a port to report, a catalog to print) hangs its own cleanup.
        """
        if self._serving.is_set():
            self._httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd.server_close()


def serve(package, split, output, *, host="127.0.0.1", port=8080,
          allow_remote=False, engine=None, registry=None, platform="unknown",
          toolchain=None, on_wiring_notes=None, log=None):
    """Admit everything, **then** bind. Returns an unstarted `OrsHttpServer`.

    The order in this function's body is the whole safety property of the verb:
    `open_adapter` either returns a fully admitted environment or raises, and
    the socket is bound on the line after it. There is no arrangement of
    failures that produces a listening server over a package that did not admit.
    """
    adapter = _ors.open_adapter(
        package, split, output, engine=engine, registry=registry,
        platform=platform, toolchain=toolchain,
        on_wiring_notes=on_wiring_notes)
    return OrsHttpServer(adapter, host=host, port=port,
                         allow_remote=allow_remote, log=log)
