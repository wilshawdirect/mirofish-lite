"""LLM adapters.

The board only needs a callable ``(system_prompt: str, user_prompt: str) -> str``.
The library never opens a network connection itself. This module ships two
adapters:

* :class:`MockLLM`  deterministic and scripted, for tests and demos.
* :class:`CmdLLM`   pipes the prompts to a local command's stdin and returns its
                    stdout, so any local model runner can be plugged in.

Both carry a ``kind`` ("mock" / "cmd") that the board records in every decision.

The callable is invoked from worker threads (up to ``max_personas_parallel`` at
a time), so it must be thread-safe, or you should set that limit to 1.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

LLM = Callable[[str, str], str]

_PERSONA_TAG_RE = re.compile(r"^\[persona: (.+?)\]", re.MULTILINE)


def persona_from_system_prompt(system_prompt: str) -> str | None:
    """The board tags every system prompt with ``[persona: <name>]``; read it back."""
    m = _PERSONA_TAG_RE.search(system_prompt or "")
    return m.group(1) if m else None


def reply(vote: str, reason: str = "scripted", confidence: float = 0.8) -> str:
    """Build a well-formed vote reply (a JSON string) for scripting a MockLLM."""
    return json.dumps({"vote": vote, "reason": reason, "confidence": confidence})


_DEFAULT_REPLY = {"vote": "YES", "reason": "mock default", "confidence": 0.5}


@dataclass(frozen=True)
class MockCall:
    persona: str | None
    system: str
    user: str


class MockLLM:
    """A deterministic, scripted stand-in for a model.

    ``responses``  maps a persona name to a reply, or to a list of replies used
                   in order (the last one repeats). A reply may be: a ``str``
                   (returned verbatim), a ``dict`` (JSON-encoded), an
                   ``Exception`` instance (raised), or a callable
                   ``(system, user) -> reply``. The key ``"*"`` sets the
                   fallback for personas without their own entry.
    ``default``    reply used when nothing else matches (default: a YES).
    ``rules``      ``[(predicate, reply), ...]`` checked first. A predicate is
                   ``(persona, system, user) -> bool``; use it to script a
                   model that reacts to text in the prompt.
    ``delay``      seconds to sleep per call (a float, or ``{persona: seconds}``).

    Every call is recorded in ``calls``; ``max_concurrent`` is the highest number
    of calls that were in flight at once.

    A MockLLM is a rubber stamp by construction: it is for tests and demos and
    is NOT a review of anything. The board records ``llm: "mock"`` in every
    decision it makes with one.
    """

    kind = "mock"

    def __init__(
        self,
        responses: Mapping[str, Any] | None = None,
        *,
        default: Any = None,
        rules: Sequence[tuple[Callable[[str | None, str, str], bool], Any]] = (),
        delay: float | Mapping[str, float] = 0.0,
    ) -> None:
        self.responses: dict[str, Any] = dict(responses or {})
        self.default = _DEFAULT_REPLY if default is None else default
        self.rules = list(rules)
        self.delay = delay
        self.calls: list[MockCall] = []
        self.max_concurrent = 0
        self._active = 0
        self._cursor: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- construction helpers -------------------------------------------------

    @classmethod
    def from_spec(cls, spec: str) -> "MockLLM":
        """Build from a CLI-style spec such as ``"risk-officer=NO,cash-flow=error,*=YES"``.

        Values: YES / NO / ABSTAIN (any case), ``error`` (the call raises) or
        ``garbage`` (the reply is not JSON). An empty spec means everyone says YES.
        """
        responses: dict[str, Any] = {}
        default = None
        for item in [p.strip() for p in spec.split(",") if p.strip()]:
            if "=" not in item:
                raise ValueError(f"bad mock spec item {item!r}; expected persona=VALUE")
            name, _, value = item.partition("=")
            name, value = name.strip(), value.strip()
            if not name:
                raise ValueError(f"bad mock spec item {item!r}: empty persona name")
            lowered = value.lower()
            if lowered == "error":
                scripted: Any = RuntimeError("scripted mock error")
            elif lowered == "garbage":
                scripted = "I am not going to answer in JSON, sorry."
            elif value.upper() in ("YES", "NO", "ABSTAIN"):
                scripted = reply(value.upper(), f"scripted {value.upper()}")
            else:
                raise ValueError(f"bad mock value {value!r} for {name!r}; use YES, NO, ABSTAIN, error or garbage")
            if name == "*":
                default = scripted
            else:
                responses[name] = scripted
        return cls(responses, default=default)

    # -- the callable -----------------------------------------------------------

    def __call__(self, system_prompt: str, user_prompt: str) -> Any:
        persona = persona_from_system_prompt(system_prompt)
        with self._lock:
            self.calls.append(MockCall(persona, system_prompt, user_prompt))
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            delay = self.delay.get(persona, 0.0) if isinstance(self.delay, Mapping) else self.delay
            if delay:
                time.sleep(delay)
            return self._resolve(self._pick(persona, system_prompt, user_prompt), system_prompt, user_prompt)
        finally:
            with self._lock:
                self._active -= 1

    def _pick(self, persona: str | None, system: str, user: str) -> Any:
        for predicate, scripted in self.rules:
            if predicate(persona, system, user):
                return scripted
        key = persona if persona in self.responses else ("*" if "*" in self.responses else None)
        if key is None:
            return self.default
        scripted = self.responses[key]
        if isinstance(scripted, (list, tuple)):
            with self._lock:
                idx = self._cursor.get(key, 0)
                self._cursor[key] = idx + 1
            return scripted[min(idx, len(scripted) - 1)]
        return scripted

    def _resolve(self, scripted: Any, system: str, user: str) -> Any:
        if isinstance(scripted, BaseException):
            raise scripted
        if callable(scripted):
            return self._resolve(scripted(system, user), system, user)
        if isinstance(scripted, dict):
            return json.dumps(scripted)
        return scripted

    def calls_for(self, persona: str) -> list[MockCall]:
        return [c for c in self.calls if c.persona == persona]


class CmdError(RuntimeError):
    """The external command exited non-zero or could not be started."""


# Every command process that is currently running, with the CmdLLM that started it
# and the thread that is waiting for it. Used to kill process trees that would
# otherwise outlive an abandoned call (timeout, deadline) or the interpreter.
_LIVE: dict[subprocess.Popen, tuple[int, int]] = {}
_LIVE_LOCK = threading.Lock()


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt" and proc.poll() is not None:
        return  # already gone (and its pid may be reused); taskkill /T cannot reach orphans anyway
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        else:
            # start_new_session made the pgid == proc.pid; the group outlives the shell while a grandchild runs
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _kill_live(owner: int | None = None, thread_ident: int | None = None) -> None:
    with _LIVE_LOCK:
        procs = [
            p
            for p, (o, t) in _LIVE.items()
            if (owner is None or o == owner) and (thread_ident is None or t == thread_ident)
        ]
    for proc in procs:
        _kill_tree(proc)


atexit.register(_kill_live)  # backstop: no command outlives the interpreter


class CmdLLM:
    """Run ``command`` through the shell; prompts go in on stdin, the reply is stdout.

    stdin format:
      ``text`` (default)  system prompt, a blank line, ``---``, a blank line, user prompt
      ``json``            ``{"system": "...", "prompt": "..."}``

    The proposal is only ever sent on stdin, never spliced into the command line.
    The command string itself comes from whoever configures the adapter and is
    run with the shell, so treat it like any other command you would type.

    The command inherits this process's full environment unless you pass ``env``
    (a complete replacement mapping). Anything in the environment, API keys
    included, is therefore visible to it.

    Process hygiene: on timeout the whole process tree is killed and
    ``TimeoutError`` is raised; a non-zero exit raises :class:`CmdError`. Live
    processes are tracked, :meth:`cancel` kills them (the board calls it for calls
    it abandons on a timeout or deadline), and an ``atexit`` hook kills any that
    remain when the interpreter exits. Set ``timeout`` a little below the board's
    ``timeout_s`` so the command is killed before the board gives up on it.
    """

    kind = "cmd"

    def __init__(
        self,
        command: str,
        *,
        timeout: float | None = 60.0,
        input_format: str = "text",
        max_output_chars: int = 200_000,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if input_format not in ("text", "json"):
            raise ValueError("input_format must be 'text' or 'json'")
        self.command = command
        self.timeout = timeout
        self.input_format = input_format
        self.max_output_chars = max_output_chars
        self.cwd = cwd
        self.env = dict(env) if env is not None else None

    def _payload(self, system_prompt: str, user_prompt: str) -> str:
        if self.input_format == "json":
            return json.dumps({"system": system_prompt, "prompt": user_prompt})
        return system_prompt + "\n\n---\n\n" + user_prompt + "\n"

    def cancel(self, thread_ident: int | None = None) -> None:
        """Kill the process trees this adapter has running (only those started from
        ``thread_ident`` when given). Safe to call at any time."""
        _kill_live(owner=id(self), thread_ident=thread_ident)

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                self.command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.cwd,
                env=self.env,
                **kwargs,
            )
        except OSError as exc:
            raise CmdError(f"could not start command ({type(exc).__name__})") from exc
        with _LIVE_LOCK:
            _LIVE[proc] = (id(self), threading.get_ident())
        try:
            try:
                out, err = proc.communicate(self._payload(system_prompt, user_prompt), timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.communicate(timeout=5)
                except (subprocess.SubprocessError, OSError, ValueError):
                    pass
                raise TimeoutError(f"command produced no reply within {self.timeout}s") from None
            if proc.returncode != 0:
                # only the status: stderr can echo the command line (paths, user names) into the ledger
                raise CmdError(f"command exited with status {proc.returncode}")
            return (out or "")[: self.max_output_chars]
        finally:
            with _LIVE_LOCK:
                _LIVE.pop(proc, None)
            _kill_tree(proc)
