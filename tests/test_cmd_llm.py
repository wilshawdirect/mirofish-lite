"""CmdLLM used as a library: environment, timeouts, and no process left behind."""

import os
import sys
import threading
import time

import pytest
from helpers import pid_alive, wait_until_dead

from mirofish_lite import Board, CmdError, CmdLLM, Persona, QuorumRule

SLEEPER = """
import os, sys, time
pid = os.getpid()
with open(os.path.join(sys.argv[1], str(pid) + ".pid"), "w") as fh:
    fh.write(str(pid))
time.sleep(300)
"""


def command(tmp_path, body, *args):
    script = tmp_path / "child.py"
    script.write_text(body)
    extra = "".join(f' "{a}"' for a in args)
    return f'"{sys.executable}" "{script}"{extra}'


def wait_for_pids(pid_dir, n=1, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pids = [int(p.stem) for p in pid_dir.glob("*.pid")]
        if len(pids) >= n:
            return pids
        time.sleep(0.05)
    raise AssertionError("child never started")


def test_kind_is_cmd():
    assert CmdLLM("echo hi").kind == "cmd"


def test_a_command_is_required():
    with pytest.raises(ValueError):
        CmdLLM("   ")


def test_prompts_arrive_on_stdin_and_stdout_is_the_reply(tmp_path):
    llm = CmdLLM(command(tmp_path, "import sys; data = sys.stdin.read(); print(data.upper())"))
    out = llm("system words", "user words")
    assert "SYSTEM WORDS" in out and "USER WORDS" in out


def test_the_command_inherits_the_environment_unless_env_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("MLITE_INHERITED_VAR", "visible-to-the-command")
    body = "import os; print(os.environ.get('MLITE_INHERITED_VAR', 'absent'), os.environ.get('MLITE_EXPLICIT_VAR', 'absent'))"
    cmd = command(tmp_path, body)
    assert CmdLLM(cmd)("s", "u").split() == ["visible-to-the-command", "absent"]
    explicit = {**os.environ, "MLITE_EXPLICIT_VAR": "given"}
    explicit.pop("MLITE_INHERITED_VAR")
    assert CmdLLM(cmd, env=explicit)("s", "u").split() == ["absent", "given"]


def test_a_nonzero_exit_raises_cmd_error_with_the_status_only(tmp_path):
    cmd = command(
        tmp_path,
        "import sys; sys.stderr.write('denied for sk-abcdef1234567890XYZ at /home/someone/runner.py'); sys.exit(3)",
    )
    with pytest.raises(CmdError) as info:
        CmdLLM(cmd)("s", "u")
    assert str(info.value) == "command exited with status 3"


def test_timeout_raises_and_kills_the_child(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    llm = CmdLLM(command(tmp_path, SLEEPER, pid_dir), timeout=1.0)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        llm("s", "u")
    assert time.monotonic() - t0 < 20
    assert wait_until_dead(wait_for_pids(pid_dir)) == []


def test_cancel_kills_a_running_child_and_unblocks_the_caller(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    llm = CmdLLM(command(tmp_path, SLEEPER, pid_dir), timeout=300)
    outcome = []

    def call():
        try:
            llm("s", "u")
        except Exception as exc:  # noqa: BLE001
            outcome.append(type(exc).__name__)

    t = threading.Thread(target=call)
    t.start()
    pids = wait_for_pids(pid_dir)
    assert all(pid_alive(p) for p in pids)
    llm.cancel()
    t.join(timeout=30)
    assert not t.is_alive() and outcome  # the caller was released with an error
    assert wait_until_dead(pids) == []


def test_cancel_only_touches_the_given_thread(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    llm = CmdLLM(command(tmp_path, SLEEPER, pid_dir), timeout=300)
    threads = [threading.Thread(target=lambda: _swallow(llm)) for _ in range(2)]
    [t.start() for t in threads]
    pids = wait_for_pids(pid_dir, n=2)
    llm.cancel(threads[0].ident)
    threads[0].join(timeout=30)
    time.sleep(0.5)
    assert threads[1].is_alive()  # the other call is untouched
    assert sum(pid_alive(p) for p in pids) == 1
    llm.cancel()
    threads[1].join(timeout=30)
    assert wait_until_dead(pids) == []


def _swallow(llm):
    try:
        llm("s", "u")
    except Exception:  # noqa: BLE001
        pass


def test_the_board_cancels_calls_it_abandons_at_the_deadline(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    llm = CmdLLM(command(tmp_path, SLEEPER, pid_dir), timeout=300)
    personas = [Persona(f"p{i}", "voter") for i in range(3)]
    board = Board(personas, llm, quorum=QuorumRule(k=2), timeout_s=300, deadline_s=4)
    decision = board.decide("some proposal")
    assert decision.outcome.value == "VETO" and {v.status for v in decision.votes} == {"deadline"}
    pids = wait_for_pids(pid_dir, n=3)
    assert wait_until_dead(pids) == []  # no command outlives the decision


def test_the_board_cancels_calls_it_abandons_on_a_persona_timeout(tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    llm = CmdLLM(command(tmp_path, SLEEPER, pid_dir), timeout=300)  # deliberately longer than the board's
    board = Board([Persona("p0", "voter")], llm, quorum=QuorumRule(k=1), timeout_s=3.0, deadline_s=60)
    decision = board.decide("some proposal")
    assert decision.votes[0].status == "timeout"
    assert wait_until_dead(wait_for_pids(pid_dir)) == []
