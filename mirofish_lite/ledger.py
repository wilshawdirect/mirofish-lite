"""Append-only JSONL ledger of decisions.

* One JSON object per line, ASCII-only (non-ASCII is ``\\u``-escaped) so no
  tool can split a record in the wrong place.
* Records are only ever appended. The library has no API that edits or deletes
  a record.
* Appends are serialised with an OS file lock (``fcntl.flock`` on POSIX,
  ``msvcrt.locking`` on Windows) on a ``<ledger>.lock`` sidecar file, so several
  processes can append to the same ledger without interleaving or losing
  records. The lock is advisory: it only coordinates writers that use this
  class, and it is not reliable on some network file systems.
* If a crash left a torn last line (no trailing newline), the next append first
  starts a new line. The torn fragment is kept and reported as ``corrupt`` by
  :meth:`Ledger.read`; the new record is intact.
* The proposal text is stored only when the ledger was created with
  ``store_proposal=True``; otherwise only its SHA-256 is kept. Raw reply
  excerpts for unparsable replies follow the same switch.
* ``archive()`` rotates the live file to a timestamped file without a window in
  which a write can be lost (see its docstring).

Append-only here is a property of this code, not of the file system: anyone who
can write to the file can still edit it. See the README threat model.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

_O_BINARY = getattr(os, "O_BINARY", 0)  # Windows: do not translate "\n" to "\r\n"
LOCK_TIMEOUT_S = 30.0


class LedgerError(RuntimeError):
    """A ledger operation failed. When archive() raises this, no record was lost."""


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    records: int
    bytes: int
    sha256: str


def _digest(path: Path) -> tuple[str, int, int]:
    """(sha256 hex, size in bytes, number of non-empty lines)"""
    h = hashlib.sha256()
    size = lines = 0
    with open(path, "rb") as f:
        for raw in f:
            h.update(raw)
            size += len(raw)
            if raw.strip():
                lines += 1
    return h.hexdigest(), size, lines


class _FileLock:
    """Cross-process exclusive lock on one byte of a sidecar file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd = -1

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        delay = 0.0005
        while True:
            try:
                self._try_lock()
                return self
            except OSError as exc:
                if time.monotonic() > deadline:
                    os.close(self.fd)
                    raise LedgerError(f"could not lock {self.path} within {LOCK_TIMEOUT_S:.0f}s: {exc}") from exc
                time.sleep(delay)
                delay = min(delay * 2, 0.004)

    def _try_lock(self) -> None:
        if fcntl is not None:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
        # else: no locking primitive on this platform; single-process safety only

    def __exit__(self, *exc_info: object) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            elif msvcrt is not None:
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self.fd)


class Ledger:
    def __init__(self, path: str | os.PathLike[str], *, store_proposal: bool = False, fsync: bool = True) -> None:
        self.path = Path(path)
        self.store_proposal = store_proposal
        self.fsync = fsync
        self._lock = threading.Lock()
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    # -- writing ----------------------------------------------------------------

    def append(self, decision: Any, proposal_text: str | None = None) -> None:
        """Append one decision. ``decision`` is a Decision (or its ``to_dict()``)."""
        data = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
        record: dict[str, Any] = {"type": "decision", **data}
        if self.store_proposal:
            if proposal_text is not None:
                record["proposal"] = proposal_text
        else:
            record["parse_failures"] = [{**pf, "excerpt": ""} for pf in record.get("parse_failures", [])]
        line = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        with self._lock, _FileLock(self._lock_path):
            fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | _O_BINARY, 0o600)
            try:
                payload = line
                if os.fstat(fd).st_size > 0:
                    os.lseek(fd, -1, os.SEEK_END)
                    if os.read(fd, 1) != b"\n":  # a torn last line: start a fresh one
                        payload = b"\n" + line
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)

    # -- reading ----------------------------------------------------------------

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield every record. A line that is not valid JSON is yielded as
        ``{"type": "corrupt", "line_no": n, "raw": "..."}`` rather than skipped."""
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            for n, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, RecursionError):
                    yield {"type": "corrupt", "line_no": n, "raw": raw[:200].decode("utf-8", "replace")}
                    continue
                if isinstance(obj, dict):
                    yield obj
                else:
                    yield {"type": "corrupt", "line_no": n, "raw": raw[:200].decode("utf-8", "replace")}

    def count(self) -> int:
        return sum(1 for _ in self.read())

    # -- rotation ---------------------------------------------------------------

    def archive(self, archive_dir: str | os.PathLike[str] | None = None) -> ArchiveResult | None:
        """Rotate the live file into ``<stem>.<UTC timestamp><suffix>``.

        1. Rename the live file to a ``*.rotating`` staging name (atomic). From
           this moment any new append creates a fresh live file, so a write is
           never lost between "copy" and "truncate".
        2. Copy the staging file to its final archive name (in ``archive_dir``
           if given, e.g. another disk) and verify the copy: SHA-256, size and
           record count must match the staging file.
        3. Only after the verification passes is the staging duplicate removed.

        If anything fails, the staging file is kept (renamed ``*.unverified``
        when the copy could not be verified) and :class:`LedgerError` is raised.
        An interrupted process can leave a ``*.rotating`` file behind; its
        content is intact and can simply be kept or renamed by hand.

        Returns ``None`` when there is nothing to archive. The rotation holds the
        same cross-process lock as :meth:`append`.
        """
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with self._lock, _FileLock(self._lock_path):
            if not self.path.exists() or self.path.stat().st_size == 0:
                return None
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            dest_dir = Path(archive_dir) if archive_dir is not None else self.path.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            staging = self.path.with_name(f"{self.path.stem}.{ts}{self.path.suffix}.rotating")
            final = dest_dir / f"{self.path.stem}.{ts}{self.path.suffix}"
            n = 0
            while final.exists():
                n += 1
                final = dest_dir / f"{self.path.stem}.{ts}-{n}{self.path.suffix}"

            try:
                os.replace(self.path, staging)  # step 1
            except OSError as exc:
                raise LedgerError(f"could not rotate {self.path}: {exc}; nothing was changed") from exc

            try:
                shutil.copyfile(staging, final)  # step 2
                src = _digest(staging)
                dst = _digest(final)
            except OSError as exc:
                self._keep_unverified(staging, None)
                raise LedgerError(f"archive copy failed: {exc}; original kept at {staging}") from exc
            if src != dst:
                kept = self._keep_unverified(staging, final)
                raise LedgerError(f"archive copy did not verify; original kept at {kept}")

            try:
                os.remove(staging)  # step 3: the verified copy now holds every record
            except OSError:
                pass  # a leftover duplicate is harmless
            try:
                with open(self.path, "ab"):
                    pass  # leave an empty live file in place; never truncates a new one
            except OSError:
                pass
            return ArchiveResult(path=final, records=src[2], bytes=src[1], sha256=src[0])

    @staticmethod
    def _keep_unverified(staging: Path, bad_copy: Path | None) -> Path:
        kept = staging.with_name(staging.name[: -len(".rotating")] + ".unverified")
        try:
            os.replace(staging, kept)
        except OSError:
            kept = staging
        if bad_copy is not None and bad_copy.exists():
            try:
                os.replace(bad_copy, bad_copy.with_name(bad_copy.name + ".failed-verify"))
            except OSError:
                pass
        return kept
