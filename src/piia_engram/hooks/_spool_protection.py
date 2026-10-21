"""Queue-only codec and read-only key acquisition. Crypto workers never publish."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

CRYPTO_BUDGET_SECONDS = 0.5
DIAGNOSTIC_RESERVE_SECONDS = 0.025
POLICY_GATE_WAIT_SECONDS = 10.0
HEADER_BYTES = 2048
DOMAIN = "engram-hook-spool-v2"
POLICY = {"schema": 1, "mode": "required"}
IMMUTABLE = ("schema", "event_id", "kind", "client", "created_at", "protection", "salt_id")
CRYPTO_CODES = frozenset({"crypto-secret-missing", "crypto-salt-missing", "crypto-salt-invalid",
    "crypto-unavailable", "crypto-timeout", "crypto-decrypt-failed", "crypto-salt-mismatch",
    "protection-unavailable", "event-oversize"})


class ProtectionError(ValueError):
    """Only closed, body-free codes cross the queue boundary."""
    def __init__(self, code: str):
        self.code = code if code in CRYPTO_CODES else "crypto-unavailable"
        super().__init__(self.code)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def loads(value: str) -> dict:
    return json.loads(value, object_pairs_hook=_pairs)


def policy_path(root: Path) -> Path:
    return root / "hooks" / "spool-protection.json"


def _present(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise ProtectionError("protection-unavailable") from None


def required(root: Path) -> bool:
    """Encryption is opt-in; any persistent signal prevents silent downgrade."""
    return (_required_marker(root) or bool(os.environ.get("ENGRAM_SECRET", "").strip())
            or _present(root / ".corpus_salt"))


def _required_marker(root: Path) -> bool:
    marker = policy_path(root)
    if _present(marker):
        try:
            if not stat.S_ISREG(marker.lstat().st_mode):
                raise ValueError("policy type")
            with marker.open("rb") as handle:
                data = handle.read(129)
            value = loads(data.decode("utf-8"))
            if len(data) > 128 or value != POLICY or type(value.get("schema")) is not int:
                raise ValueError("policy shape")
        except (OSError, ValueError, TypeError, AttributeError):
            raise ProtectionError("protection-unavailable") from None
        return True
    return False


class PolicyGateBusy(ProtectionError):
    def __init__(self):
        super().__init__("protection-unavailable")


class _WindowsPolicyLocker:
    """Use the same byte at offset zero for both modes, on every portalocker version."""
    def __init__(self, handle):
        import msvcrt
        import pywintypes
        self.handle = msvcrt.get_osfhandle(handle.fileno())
        self.offset = pywintypes.OVERLAPPED()

    def lock(self, shared: bool):
        import portalocker
        import pywintypes
        import win32file
        import winerror
        try:
            # FAIL_IMMEDIATELY, and EXCLUSIVE_LOCK only for the exclusive mode.
            win32file.LockFileEx(self.handle, 1 if shared else 3, 1, 0, self.offset)
        except pywintypes.error as exc:
            if exc.winerror == winerror.ERROR_LOCK_VIOLATION:
                raise portalocker.AlreadyLocked() from None
            raise

    def unlock(self):
        import win32file
        win32file.UnlockFileEx(self.handle, 1, 0, self.offset)


@contextmanager
def policy_gate(root: Path, *, timeout: float = 0, shared: bool = False):
    """Root-local cross-process gate.

    Plaintext publishers hold it shared, so they never exclude each other;
    encryption enablement and marker/salt creation hold it exclusively.
    """
    import portalocker
    handle = None
    locked = False
    try:
        directory = root / "hooks"
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / ".spool-policy.lock").open("ab")
        # Select per handle: never change portalocker's process-global LOCKER.
        locker = _WindowsPolicyLocker(handle) if os.name == "nt" else None
        end = time.monotonic() + max(0, timeout)
        while True:
            try:
                mode = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
                if locker is not None:
                    locker.lock(shared)
                else:
                    portalocker.lock(handle, mode | portalocker.LOCK_NB)
                locked = True
                break
            except portalocker.LockException:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise PolicyGateBusy() from None
                time.sleep(min(0.01, remaining))
    except Exception as exc:
        if handle is not None:
            handle.close()
        if isinstance(exc, ProtectionError):
            raise
        raise ProtectionError("protection-unavailable") from None
    try:
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    if locker is not None:
                        locker.unlock()
                    else:
                        portalocker.unlock(handle)
            finally:
                handle.close()


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline - DIAGNOSTIC_RESERVE_SECONDS:
        raise ProtectionError("crypto-timeout")


def capture_deadline(deadline: float) -> float:
    """One sealing/policy allowance, including transition reseals and checks."""
    return min(deadline, time.monotonic() + CRYPTO_BUDGET_SECONDS + DIAGNOSTIC_RESERVE_SECONDS)


def _ensure_required_locked(root: Path, deadline: float | None = None) -> None:
    """Caller owns the policy gate; all writes stay on the calling thread."""
    try:
        present = (_required_marker(root) if deadline is None else
                   _with_budget(lambda: _required_marker(root), deadline))
        if present:
            return
        from .spool import _publish
        if deadline is not None:
            check_deadline(deadline)
        _publish(policy_path(root), json_bytes(POLICY))
    except ProtectionError:
        raise
    except Exception:
        raise ProtectionError("protection-unavailable") from None


def ensure_required(root: Path, *, deadline: float | None = None) -> None:
    """Bounded writable enablement; marker creation shares the publication gate."""
    if deadline is not None:
        check_deadline(deadline)
    present = (_required_marker(root) if deadline is None else
               _with_budget(lambda: _required_marker(root), deadline))
    if present:
        return
    wait = POLICY_GATE_WAIT_SECONDS if deadline is None else max(
        0, deadline - DIAGNOSTIC_RESERVE_SECONDS - time.monotonic())
    with policy_gate(root, timeout=wait):
        _ensure_required_locked(root, deadline)


def required_with_budget(root: Path, deadline: float) -> bool:
    return _with_budget(lambda: required(root), deadline)


def read_salt(root: Path) -> bytes:
    path = root / ".corpus_salt"
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ProtectionError("crypto-salt-invalid")
        with path.open("rb") as handle:
            salt = handle.read(17)
        if len(salt) != 16:
            raise ProtectionError("crypto-salt-invalid")
        return salt
    except FileNotFoundError:
        raise ProtectionError("crypto-salt-missing") from None
    except OSError:
        raise ProtectionError("crypto-salt-invalid") from None


class QueueCodec:
    """One memory-only key per invocation; working objects never become wire objects."""
    def __init__(self, root: Path):
        self.root = root
        self._material = None

    def ready(self):
        if self._material is None:
            secret = os.environ.get("ENGRAM_SECRET", "").strip()
            if not secret:
                raise ProtectionError("crypto-secret-missing")
            salt = read_salt(self.root)
            try:
                from ..crypto import EncryptionEngine
                engine = EncryptionEngine(secret)
                if not engine.enabled:
                    raise ValueError("disabled")
                key = engine.derive_corpus_key(salt)
                if not isinstance(key, bytes) or len(key) != 32:
                    raise ValueError("key")
            except Exception:
                raise ProtectionError("crypto-unavailable") from None
            self._material = engine, key, hashlib.sha256(salt).hexdigest()
        return self._material

    def encode(self, event: dict) -> bytes:
        from .spool import MAX_EVENT_BYTES
        protected = event.get("schema") == 2 or required(self.root)
        if not protected:
            data = json_bytes(event)
        else:
            engine, key, salt_id = self.ready()
            header = {name: event[name] for name in IMMUTABLE[:5]}
            header.update(schema=2, protection="enc:v2c", salt_id=salt_id)
            body = dict(domain=DOMAIN, **header, payload=event["payload"])
            if "prepared" in event:
                body["prepared"] = event["prepared"]
            raw = json_bytes(body).decode("utf-8")
            if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
                raise ProtectionError("event-oversize")
            try:
                sealed = engine.corpus_encrypt(raw, key)
                if not isinstance(sealed, str) or not sealed.startswith("enc:v2c:"):
                    raise ValueError("passthrough")
            except Exception:
                raise ProtectionError("crypto-unavailable") from None
            envelope = dict(header)
            if "transcript_missing_attempts" in event:
                attempts = event["transcript_missing_attempts"]
                if type(attempts) is not int or not 0 <= attempts <= 3:
                    raise ProtectionError("crypto-decrypt-failed")
                envelope["transcript_missing_attempts"] = attempts
            if len(json_bytes(envelope)) > HEADER_BYTES:
                raise ProtectionError("event-oversize")
            envelope["body"] = sealed
            data = json_bytes(envelope)
        if len(data) > MAX_EVENT_BYTES:
            raise ProtectionError("event-oversize")
        return data

    def decode(self, wire: dict) -> dict:
        from .spool import MAX_EVENT_BYTES
        if type(wire.get("schema")) is int and wire["schema"] == 1:
            if required(self.root):
                self.ready()  # fail before preparing legacy input without keys
            return wire
        try:
            allowed = set(IMMUTABLE) | {"body", "transcript_missing_attempts"}
            if (set(wire) - allowed or set(IMMUTABLE) - set(wire) or
                    type(wire["schema"]) is not int or wire["schema"] != 2 or
                    wire["protection"] != "enc:v2c" or
                    not isinstance(wire["salt_id"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", wire["salt_id"])):
                raise ValueError("envelope")
            header = {name: wire[name] for name in wire if name != "body"}
            if len(json_bytes(header)) > HEADER_BYTES:
                raise ValueError("header")
            attempts = wire.get("transcript_missing_attempts", 0)
            if type(attempts) is not int or not 0 <= attempts <= 3:
                raise ValueError("counter")
            encrypted = wire["body"]
            if not isinstance(encrypted, str) or not encrypted.startswith("enc:v2c:"):
                raise ValueError("ciphertext")
            if len(encrypted) > MAX_EVENT_BYTES:
                raise ValueError("ciphertext size")
            encoded = encrypted[8:]
            binary = base64.b64decode(encoded, altchars=b"-_", validate=True)
            if len(binary) < 28 or base64.urlsafe_b64encode(binary).decode("ascii") != encoded:
                raise ValueError("ciphertext encoding")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProtectionError("crypto-decrypt-failed") from None
        required(self.root)  # validate existing policy even if the envelope signals encryption
        engine, key, salt_id = self.ready()
        if salt_id != wire["salt_id"]:
            raise ProtectionError("crypto-salt-mismatch")
        try:
            raw = engine.corpus_decrypt(encrypted, key, strict=True)
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
                raise ValueError("body size")
            body = loads(raw)
            if (not isinstance(body, dict) or body.get("domain") != DOMAIN or
                    set(body) - (set(IMMUTABLE) | {"domain", "payload", "prepared"}) or
                    any(type(body.get(k)) is not type(wire[k]) or body[k] != wire[k] for k in IMMUTABLE) or
                    not isinstance(body.get("payload"), dict)):
                raise ValueError("binding")
        except Exception:
            raise ProtectionError("crypto-decrypt-failed") from None
        event = {k: wire[k] for k in IMMUTABLE}
        event["payload"] = body["payload"]
        if "prepared" in body:
            event["prepared"] = body["prepared"]
        if "transcript_missing_attempts" in wire:
            event["transcript_missing_attempts"] = attempts
        return event


def seal_with_budget(root: Path, event: dict, deadline: float) -> tuple[bytes, bool]:
    """Abandoned workers can only finish in memory; no filesystem write capability."""
    value = _with_budget(lambda: QueueCodec(root).encode(event), capture_deadline(deadline))
    return value, loads(value.decode("utf-8"))["schema"] == 2


def _with_budget(operation, deadline: float):
    """Policy reads and both encoders run inside the same read-only wait."""
    check_deadline(deadline)
    end = deadline - DIAGNOSTIC_RESERVE_SECONDS
    result = []
    finished = threading.Event()
    def seal():
        try:
            result.append((True, operation()))
        except ProtectionError as exc:
            result.append((False, exc.code))
        except Exception:
            result.append((False, "crypto-unavailable"))
        finally:
            finished.set()
    threading.Thread(target=seal, name="engram-hook-seal", daemon=True).start()
    if not finished.wait(max(0, end - time.monotonic())) or time.monotonic() > end:
        raise ProtectionError("crypto-timeout")
    ok, value = result[0]
    if not ok:
        raise ProtectionError(value)
    return value


def queue_needs_original_salt(root: Path) -> bool:
    """Core guard includes queue-only restores and interrupted ciphertext publications."""
    if _present(policy_path(root)):
        return True
    directory = root / "hooks" / "spool"
    try:
        for path in directory.rglob("*"):
            if path.is_file() and not path.is_symlink() and path.suffix in {".jsonl", ".partial"}:
                with path.open("rb") as handle:
                    # Core recovery must also protect damaged/older envelopes,
                    # even when ciphertext is beyond the normal header budget.
                    tail = b""
                    while chunk := handle.read(65536):
                        sample = tail + chunk
                        if b"enc:v2c" in sample or re.search(rb'"schema"\s*:\s*2\b', sample):
                            return True
                        tail = sample[-32:]
    except OSError:
        raise ProtectionError("protection-unavailable") from None
    return False
