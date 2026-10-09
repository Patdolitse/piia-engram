"""Stable transport-loss guidance shared by observable local boundaries."""
from __future__ import annotations
import errno
import functools
import inspect
import json

class TransportUnavailable(RuntimeError):
    """The MCP session/transport cannot accept another write."""


def transport_failure(exc, *, idempotency_key=""):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        known = isinstance(exc, (TransportUnavailable, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, EOFError))
        known = known or (type(exc).__module__.startswith("anyio") and type(exc).__name__ in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"})
        known = known or (isinstance(exc, OSError) and exc.errno in {errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED})
        for child in getattr(exc, "exceptions", ()):
            grouped = transport_failure(child, idempotency_key=idempotency_key)
            if grouped:
                return grouped
        text = str(exc).lower()
        known = known or (isinstance(exc, RuntimeError) and any(s in text for s in (
            "session terminated", "session closed", "server is shutting down", "server shutting down")))
        if known:
            return {"error": "transport_unavailable", "retryable": True,
                    "retry_same_key": bool(idempotency_key),
                    "completion": "unknown",
                    "hint": "Restart the client or rerun the command. If an idempotency key exists, reuse the same key; query operation status before retrying. Without a key, check whether the write completed before repeating it."}
        exc = exc.__cause__ or exc.__context__
    return None


def guarded_tool(fn, *, shutting_down=lambda: False):
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    async def guarded(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        key = bound.arguments.get("idempotency_key", "")
        try:
            if shutting_down():
                raise TransportUnavailable("server is shutting down")
            result = await fn(*args, **kwargs)
        except Exception as exc:
            failure = transport_failure(exc, idempotency_key=key)
            if failure:
                return json.dumps(failure)
            if getattr(exc, "code", "") == "migration_required":
                return json.dumps({"error": exc.code, "hint": "Run engram migrate-project <project> locally; preview and back up before applying."})
            raise
        # Some tools record partial closeout stages before returning an error.
        if isinstance(result, str) and "transport_unavailable:" in result:
            try:
                payload = json.loads(result)
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(transport_failure(TransportUnavailable(), idempotency_key=key))
            return json.dumps(payload)
        return result
    return guarded
