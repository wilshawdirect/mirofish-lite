"""Shared helpers for the test-suite."""

from __future__ import annotations

import os
import sys
import time

from mirofish_lite import Board, MockLLM, Persona, QuorumRule, Vote, reply

__all__ = ["board", "make_vote", "pid_alive", "reply", "simple_personas", "wait_until_dead"]


def simple_personas(n: int = 5, **overrides) -> list[Persona]:
    return [Persona(f"p{i}", f"You are test voter number {i}.", **overrides) for i in range(n)]


def board(
    responses=None, *, n: int = 5, k: float | None = None, personas=None, default=None, rules=(), delay=0.0, **kwargs
):
    """Build a Board plus its MockLLM. Default: 5 personas, k = n - 1, everyone says YES."""
    personas = list(personas) if personas is not None else simple_personas(n)
    llm = MockLLM(responses or {}, default=default, rules=rules, delay=delay)
    if k is None:
        k = max(1, len([p for p in personas if p.in_quorum]) - 1)
    quorum = kwargs.pop("quorum", QuorumRule(k=k))
    return Board(personas, llm, quorum=quorum, **kwargs), llm


def make_vote(persona="p0", vote="YES", *, weight=1, in_quorum=True, hard_veto=False, status="ok") -> Vote:
    return Vote(
        persona=persona,
        vote=vote,
        reason="r",
        confidence=0.5,
        status=status,
        weight=weight,
        in_quorum=in_quorum,
        hard_veto=hard_veto,
    )


def pid_alive(pid: int) -> bool:
    """Is a process with this pid running? (Never signals the process.)"""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is dead for our purposes
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_until_dead(pids, timeout: float = 15.0) -> list[int]:
    """Poll until none of the pids is alive; return the ones still alive after the timeout."""
    deadline = time.monotonic() + timeout
    alive = [p for p in pids if pid_alive(p)]
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [p for p in alive if pid_alive(p)]
    return alive
