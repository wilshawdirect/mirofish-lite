import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import wait_until_dead

import mirofish_lite.cli as cli
from mirofish_lite.cli import main

ROOT = Path(__file__).resolve().parent.parent

PROPOSAL = {"title": "Raise the cache TTL", "environment": "staging"}
BANNER = "MOCK MODEL - NOT A REVIEW"


@pytest.fixture
def proposal(tmp_path):
    p = tmp_path / "proposal.json"
    p.write_text(json.dumps(PROPOSAL))
    return p


def run(*argv):
    out = io.StringIO()
    code = main([str(a) for a in argv], stdout=out)
    return code, out.getvalue()


def py_script(tmp_path, body, name="model.py", *args):
    path = tmp_path / name
    path.write_text(body)
    extra = "".join(f' "{a}"' for a in args)
    return f'"{sys.executable}" "{path}"{extra}'


def module_run(tmp_path, *args, env_extra=None, **kwargs):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "mirofish_lite", *map(str, args)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        **kwargs,
    )


# --- --llm is required; a mock is labelled ------------------------------------------------


def test_llm_is_required(proposal, capsys):
    code, out = run("decide", proposal)
    err = capsys.readouterr().err
    assert code == 3 and out == ""
    assert "--llm is required" in err and "mock" in err


def test_the_advisors_rubber_stamp_repro_no_longer_approves(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("DROP TABLE customers; rm -rf /; no rollback")
    proc = module_run(tmp_path, "decide", bad)
    assert proc.returncode == 3
    assert "APPROVE" not in proc.stdout
    assert "--llm is required" in proc.stderr


@pytest.mark.parametrize("llm", ["mock", "mock:risk-officer=NO", "mock:*=NO"])
def test_every_mock_run_says_so_on_stderr(proposal, capsys, llm):
    run("decide", proposal, "--llm", llm)
    assert BANNER in capsys.readouterr().err


def test_the_banner_goes_to_stderr_not_into_machine_readable_stdout(proposal, capsys):
    code, out = run("decide", proposal, "--llm", "mock", "--json")
    captured = capsys.readouterr()
    assert BANNER in captured.err and BANNER not in out
    assert json.loads(out)["config"]["llm"] == "mock"


def test_a_real_command_prints_no_mock_banner(proposal, tmp_path, capsys):
    cmd = py_script(tmp_path, 'print(\'{"vote": "YES"}\')')
    run("decide", proposal, "--llm", f"cmd:{cmd}")
    assert BANNER not in capsys.readouterr().err


def test_the_summary_names_the_model(proposal):
    assert "model mock" in run("decide", proposal, "--llm", "mock")[1]


def test_the_model_kind_is_recorded_in_the_ledger_but_never_the_command(proposal, tmp_path):
    ledger = tmp_path / "led.jsonl"
    run("decide", proposal, "--llm", "mock", "--ledger", ledger)
    secret_cmd = py_script(tmp_path, 'print(\'{"vote": "YES"}\')', "model.py", "--api-key=sk-supersecret1234567890")
    run("decide", proposal, "--llm", f"cmd:{secret_cmd}", "--ledger", ledger)
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [r["config"]["llm"] for r in records] == ["mock", "cmd"]
    assert "supersecret" not in ledger.read_text() and "model.py" not in ledger.read_text()


def test_explain_labels_a_mock_decision(proposal, tmp_path):
    target = tmp_path / "decision.json"
    run("decide", proposal, "--llm", "mock", "--out", target)
    text = run("explain", target)[1]
    assert BANNER in text and "Model:     mock" in text


# --- exit codes ---------------------------------------------------------------------------


def test_exit_0_on_approve(proposal):
    code, out = run("decide", proposal, "--llm", "mock")
    assert code == 0 and out.startswith("APPROVE")


def test_exit_1_on_veto(proposal):
    code, out = run("decide", proposal, "--llm", "mock:risk-officer=NO,cash-flow=NO")
    assert code == 1 and out.startswith("VETO")
    assert "risk-officer" in out and "cash-flow" in out  # the dissent report


def test_exit_2_on_no_quorum(proposal):
    code, out = run("decide", proposal, "--llm", "mock:risk-officer=ABSTAIN,cash-flow=ABSTAIN")
    assert code == 2 and out.startswith("NO_QUORUM")


def test_a_failing_persona_is_a_no_so_two_failures_veto(proposal):
    assert run("decide", proposal, "--llm", "mock:conservative=error,growth=garbage")[0] == 1


def test_on_error_abstain_turns_the_same_failures_into_no_quorum(proposal):
    assert run("decide", proposal, "--llm", "mock:conservative=error,growth=garbage", "--on-error", "abstain")[0] == 2


def test_on_error_no_is_accepted(proposal):
    assert run("decide", proposal, "--llm", "mock:conservative=error,growth=garbage", "--on-error", "no")[0] == 1


def test_there_is_no_fail_open_on_error_option(proposal, capsys):
    assert run("decide", proposal, "--llm", "mock", "--on-error", "yes")[0] == 3
    assert "invalid choice" in capsys.readouterr().err


def test_a_dead_model_can_never_approve_from_the_cli(proposal):
    # the advisor's repro: every call fails, and the old --on-error yes turned that into APPROVE
    for policy in ("veto", "no", "abstain"):
        code, out = run("decide", proposal, "--llm", "cmd:exit 1", "--on-error", policy)
        assert code in (1, 2), (policy, out)
        assert "substituted 6" in out


def test_the_summary_counts_substituted_votes(proposal):
    code, out = run("decide", proposal, "--llm", "mock:conservative=error,growth=garbage")
    assert "substituted 2" in out and "warning: 2 vote(s) were substituted" in out
    assert "substituted 0" in run("decide", proposal, "--llm", "mock")[1]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["frobnicate"],
        ["decide"],
        ["decide", "missing-file.json", "--llm", "mock"],
        ["decide", "{proposal}", "--llm", "openai"],
        ["decide", "{proposal}", "--llm", "mock:nonsense"],
        ["decide", "{proposal}", "--llm", "mock:growth=MAYBE"],
        ["decide", "{proposal}", "--llm", "cmd:"],
        ["decide", "{proposal}", "--llm", "mock", "--k", "99"],
        ["decide", "{proposal}", "--llm", "mock", "--parallel", "0"],
        ["decide", "{proposal}", "--llm", "mock", "--timeout", "-1"],
        ["decide", "{proposal}", "--llm", "mock", "--no-such-flag"],
        ["decide", "{proposal}", "--llm", "mock", "--on-error", "shrug"],
        ["decide", "{proposal}", "--llm", "mock", "--personas", "missing-personas.json"],
        ["explain", "missing.json"],
    ],
)
def test_exit_3_on_usage_errors(argv, proposal, capsys):
    code, _ = run(*[a.replace("{proposal}", str(proposal)) for a in argv])
    assert code == 3
    assert capsys.readouterr().err  # something was explained on stderr


def test_invalid_json_proposal_is_a_usage_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert run("decide", bad, "--llm", "mock")[0] == 3


def test_empty_proposal_is_a_usage_error(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text('""')
    assert run("decide", empty, "--llm", "mock")[0] == 3


def test_a_deeply_nested_proposal_is_a_clean_usage_error(tmp_path, capsys):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000)
    code, _ = run("decide", deep, "--llm", "mock")
    err = capsys.readouterr().err
    assert code == 3 and "nested too deeply" in err and "Traceback" not in err


def test_a_deeply_nested_personas_file_is_a_clean_usage_error(proposal, tmp_path):
    deep = tmp_path / "personas.json"
    deep.write_text("[" * 100_000 + "]" * 100_000)
    assert run("decide", proposal, "--llm", "mock", "--personas", deep)[0] == 3


def test_a_huge_persona_weight_is_a_clean_usage_error(proposal, tmp_path):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps([{"name": "a", "system_prompt": "x", "weight": 10**400}]))
    assert run("decide", proposal, "--llm", "mock", "--personas", personas)[0] == 3


def test_plain_text_proposal_files_are_accepted(tmp_path):
    txt = tmp_path / "proposal.txt"
    txt.write_text("Rotate the signing keys on Friday.")
    assert run("decide", txt, "--llm", "mock")[0] == 0


def test_proposal_can_come_from_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Rotate the signing keys on Friday."))
    assert run("decide", "-", "--llm", "mock")[0] == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(PROPOSAL)))
    assert run("decide", "-", "--llm", "mock:risk-officer=NO,cash-flow=NO")[0] == 1


def test_version_flag_exits_zero(capsys):
    assert run("--version")[0] == 0


# --- errors are never mistaken for verdicts ---------------------------------------------------


def test_an_unexpected_exception_exits_3_not_1(monkeypatch, capsys):
    def boom(args, out):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(cli, "_cmd_board", boom)
    assert run("board")[0] == 3
    assert "unexpected RuntimeError" in capsys.readouterr().err


def test_a_broken_stdout_exits_3_not_1(proposal):
    class Broken(io.StringIO):
        def write(self, s):
            raise BrokenPipeError("reader went away")

    assert main(["decide", str(proposal), "--llm", "mock"], stdout=Broken()) == 3


def test_a_ledger_that_cannot_be_written_exits_3_not_0(proposal, tmp_path, capsys):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a directory")
    code, out = run("decide", proposal, "--llm", "mock", "--ledger", blocker / "d.jsonl")
    assert code == 3 and "NOT approved" in capsys.readouterr().err and out == ""


ARROW_MODEL = """
import json, sys
data = sys.stdin.read()
if "[persona: contrarian]" in data:
    print(json.dumps({"vote": "NO", "reason": "check the \\u2192 rollback path", "confidence": 0.9}))
else:
    print(json.dumps({"vote": "YES", "reason": "fine", "confidence": 0.9}))
"""


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_a_reason_the_console_cannot_encode_does_not_crash_through_a_real_pipe(proposal, tmp_path, encoding):
    # The advisory contrarian says NO with an arrow in its reason; everyone else says YES, so the verdict
    # is APPROVE. Printing the dissent used to raise UnicodeEncodeError on a narrow console: exit 1 (VETO)
    # for a decision the ledger already recorded as APPROVE.
    cmd = py_script(tmp_path, ARROW_MODEL)
    ledger = tmp_path / "led.jsonl"
    proc = module_run(
        tmp_path, "decide", proposal, "--llm", f"cmd:{cmd}", "--ledger", ledger,
        env_extra={"PYTHONIOENCODING": encoding},
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "UnicodeEncodeError" not in proc.stderr and "Traceback" not in proc.stderr
    assert proc.stdout.startswith("APPROVE") and "rollback path" in proc.stdout
    assert "\\u2192" in proc.stdout  # backslash-replaced, not silently dropped
    assert json.loads(ledger.read_text().splitlines()[0])["outcome"] == "APPROVE"


# --- terminal escapes in reasons --------------------------------------------------------------------

ESCAPE_MODEL = """
import json, sys
esc = chr(27)
reason = esc + "[2J" + esc + "[H" + esc + "[32mAPPROVE all clear" + esc + "[0m" + esc + "]0;pwned" + chr(7)
print(json.dumps({"vote": "NO", "reason": reason, "confidence": 1}))
"""


def test_terminal_escapes_in_a_models_reason_never_reach_the_output(proposal, tmp_path):
    cmd = py_script(tmp_path, ESCAPE_MODEL)
    proc = module_run(tmp_path, "decide", proposal, "--llm", f"cmd:{cmd}")
    assert proc.returncode == 1
    assert "\x1b" not in proc.stdout and "\x07" not in proc.stdout and "pwned" not in proc.stdout
    assert "APPROVE all clear" in proc.stdout  # the text survives, the control sequences do not
    as_json = module_run(tmp_path, "decide", proposal, "--llm", f"cmd:{cmd}", "--json")
    assert "\x1b" not in as_json.stdout and json.loads(as_json.stdout)["votes"][0]["reason"] == "APPROVE all clear"


def test_explain_cleans_a_crafted_decision_file(proposal, tmp_path):
    target = tmp_path / "decision.json"
    run("decide", proposal, "--llm", "mock:risk-officer=NO", "--out", target)
    data = json.loads(target.read_text())
    data["votes"][0]["reason"] = chr(27) + "[2J" + chr(27) + "[32mAPPROVE" + chr(27) + "[0m"
    data["dissent"][0]["reason"] = chr(27) + "[31mspoof"
    target.write_text(json.dumps(data))
    text = run("explain", target)[1]
    assert "\x1b" not in text and "spoof" in text


# --- output options -----------------------------------------------------------------------


def test_json_output_is_the_full_decision(proposal):
    code, out = run("decide", proposal, "--llm", "mock", "--json")
    data = json.loads(out)
    assert code == 0 and data["outcome"] == "APPROVE" and len(data["votes"]) == 6
    assert set(data) >= {
        "outcome",
        "votes",
        "tally",
        "dissent",
        "parse_failures",
        "quorum_rule",
        "proposal_hash",
        "ts",
        "calls",
        "substituted",
        "config",
    }


def test_out_file_receives_the_decision(proposal, tmp_path):
    target = tmp_path / "decision.json"
    run("decide", proposal, "--llm", "mock", "--out", target)
    assert json.loads(target.read_text())["outcome"] == "APPROVE"


def test_ledger_flag_appends_and_hides_the_proposal_by_default(proposal, tmp_path):
    ledger = tmp_path / "led.jsonl"
    run("decide", proposal, "--llm", "mock", "--ledger", ledger)
    run("decide", proposal, "--llm", "mock", "--ledger", ledger)
    lines = ledger.read_text().splitlines()
    assert len(lines) == 2 and "Raise the cache TTL" not in ledger.read_text()


def test_store_proposal_flag_opts_in(proposal, tmp_path):
    ledger = tmp_path / "led.jsonl"
    run("decide", proposal, "--llm", "mock", "--ledger", ledger, "--store-proposal")
    assert "Raise the cache TTL" in ledger.read_text()


def test_custom_personas_file(proposal, tmp_path):
    personas = tmp_path / "personas.json"
    personas.write_text(
        json.dumps(
            [
                {"name": "a", "system_prompt": "Voter a."},
                {"name": "b", "system_prompt": "Voter b."},
                {"name": "guard", "system_prompt": "Guard.", "hard_veto": True},
            ]
        )
    )
    assert run("decide", proposal, "--llm", "mock", "--personas", personas)[0] == 3  # under the default k: needs --k
    assert run("decide", proposal, "--llm", "mock", "--personas", personas, "--k", "3")[0] == 0
    code, out = run("decide", proposal, "--llm", "mock:guard=NO", "--personas", personas, "--k", "3")
    assert code == 1 and "hard veto" in out


def test_k_flag_changes_the_threshold(proposal):
    two_no = "mock:growth=NO,conservative=NO"
    assert run("decide", proposal, "--llm", two_no)[0] == 1  # default k=4: two NO make it unreachable
    assert run("decide", proposal, "--llm", two_no, "--k", "3")[0] == 0  # k=3: 3 YES still outnumber 2 NO
    assert run("decide", proposal, "--llm", "mock:growth=ABSTAIN", "--k", "5")[0] == 2


def test_default_k_scales_for_a_bigger_custom_board(proposal, tmp_path):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps([{"name": f"p{i}", "system_prompt": "Voter."} for i in range(10)]))
    spec = "mock:p4=NO,p5=NO,p6=NO,p7=ABSTAIN,p8=ABSTAIN,p9=ABSTAIN"
    code, out = run("decide", proposal, "--llm", spec, "--personas", personas)
    assert code != 0 and "need 7" in out  # 4 YES / 3 NO / 3 ABSTAIN no longer approves


# --- board / explain --------------------------------------------------------------------------


def test_board_lists_the_six_personas():
    code, out = run("board")
    assert code == 0
    for name in ("conservative", "growth", "risk-officer", "cash-flow", "contrarian", "newcomer"):
        assert name in out
    assert "advisory" in out


def test_board_json_and_full():
    code, out = run("board", "--json")
    assert code == 0 and len(json.loads(out)) == 6
    _, full = run("board", "--full")
    assert "Lean NO when" in full


def test_explain_a_saved_decision(proposal, tmp_path):
    target = tmp_path / "decision.json"
    run("decide", proposal, "--llm", "mock:risk-officer=NO,cash-flow=NO", "--out", target)
    code, out = run("explain", target)
    assert code == 0
    assert "VETO" in out and "Dissent" in out and "risk-officer" in out and "below k" in out
    assert "Substituted votes" in out


def test_explain_reads_the_last_ledger_record_by_default(proposal, tmp_path):
    ledger = tmp_path / "led.jsonl"
    run("decide", proposal, "--llm", "mock", "--ledger", ledger)
    run("decide", proposal, "--llm", "mock:risk-officer=NO,cash-flow=NO", "--ledger", ledger)
    assert "VETO" in run("explain", ledger)[1]
    assert "APPROVE" in run("explain", ledger, "--index", "0")[1]
    assert run("explain", ledger, "--index", "9")[0] == 3


def test_explain_index_is_honoured_on_a_one_record_ledger(proposal, tmp_path):
    ledger = tmp_path / "one.jsonl"
    run("decide", proposal, "--llm", "mock", "--ledger", ledger)
    assert "APPROVE" in run("explain", ledger)[1]
    assert run("explain", ledger, "--index", "99")[0] == 3


def test_a_failing_command_leaves_no_trace_of_its_stderr_in_the_ledger(proposal, tmp_path):
    ledger = tmp_path / "led.jsonl"
    cmd = py_script(
        tmp_path, "import sys; sys.stderr.write('cannot open /home/someone/private_runner.py'); sys.exit(2)"
    )
    assert run("decide", proposal, "--llm", f"cmd:{cmd}", "--ledger", ledger)[0] == 1
    text = ledger.read_text()
    assert "private_runner" not in text and "someone" not in text and "status 2" in text


def test_explain_rejects_files_that_are_not_decisions(tmp_path):
    other = tmp_path / "other.json"
    other.write_text('{"hello": "world"}')
    assert run("explain", other)[0] == 3
    junk = tmp_path / "junk.jsonl"
    junk.write_text("not json at all\n")
    assert run("explain", junk)[0] == 3


# --- cmd adapter --------------------------------------------------------------------------------


MODEL_SCRIPT = """
import json, sys
data = sys.stdin.read()
ok = "[persona:" in data and "<<<PROPOSAL_DATA" in data and "Raise the cache TTL" in data
print(json.dumps({"vote": "YES" if ok else "NO", "reason": "read %d chars on stdin" % len(data), "confidence": 0.9}))
"""


def test_cmd_adapter_pipes_prompts_to_stdin(proposal, tmp_path):
    cmd = py_script(tmp_path, MODEL_SCRIPT)
    code, out = run("decide", proposal, "--llm", f"cmd:{cmd}", "--json")
    data = json.loads(out)
    assert code == 0, out
    assert all(v["status"] == "ok" and "chars on stdin" in v["reason"] for v in data["votes"])


def test_cmd_adapter_json_stdin_format(proposal, tmp_path):
    script = """
import json, sys
payload = json.load(sys.stdin)
ok = payload["system"].startswith("[persona:") and "PROPOSAL_DATA" in payload["prompt"]
print(json.dumps({"vote": "YES" if ok else "NO", "reason": "json mode", "confidence": 1}))
"""
    cmd = py_script(tmp_path, script)
    assert run("decide", proposal, "--llm", f"cmd:{cmd}", "--cmd-format", "json")[0] == 0


def test_cmd_that_exits_non_zero_is_fail_closed(proposal, tmp_path):
    cmd = py_script(tmp_path, "import sys; sys.stderr.write('model crashed'); sys.exit(7)")
    code, out = run("decide", proposal, "--llm", f"cmd:{cmd}", "--json")
    data = json.loads(out)
    assert code == 1
    assert all(v["status"] == "error" and "status 7" in v["error"] for v in data["votes"])


def test_cmd_that_prints_garbage_is_fail_closed(proposal, tmp_path):
    cmd = py_script(tmp_path, "print('I would say yes, probably')")
    code, out = run("decide", proposal, "--llm", f"cmd:{cmd}", "--json")
    assert code == 1 and len(json.loads(out)["parse_failures"]) == 6


def test_cmd_that_hangs_is_killed_at_the_timeout(proposal, tmp_path):
    cmd = py_script(tmp_path, "import time; time.sleep(30)")
    t0 = time.monotonic()
    code, out = run("decide", proposal, "--llm", f"cmd:{cmd}", "--timeout", "0.7", "--json")
    assert time.monotonic() - t0 < 15
    assert code == 1
    assert {v["status"] for v in json.loads(out)["votes"]} == {"timeout"}


def test_a_command_that_does_not_exist_is_fail_closed(proposal):
    code, out = run("decide", proposal, "--llm", "cmd:this-command-does-not-exist-xyz", "--json")
    assert code == 1 and json.loads(out)["errors"]


def test_the_proposal_is_never_part_of_the_command_line(proposal, tmp_path):
    marker = tmp_path / "argv.txt"
    script = f"""
import sys
open({str(marker)!r}, "w").write(repr(sys.argv))
print('{{"vote": "YES"}}')
"""
    cmd = py_script(tmp_path, script)
    run("decide", proposal, "--llm", f"cmd:{cmd}")
    assert "Raise the cache TTL" not in marker.read_text()


# --- no command process outlives the run --------------------------------------------------------------

SLEEPER = """
import os, sys, time
pid = os.getpid()
with open(os.path.join(sys.argv[1], str(pid) + ".pid"), "w") as fh:
    fh.write(str(pid))
time.sleep(300)
"""


def _sleeper_pids(pid_dir):
    return [int(p.stem) for p in Path(pid_dir).glob("*.pid")]


def test_children_are_dead_after_a_per_persona_timeout(proposal, tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    cmd = py_script(tmp_path, SLEEPER, "sleeper.py", pid_dir)
    proc = module_run(
        tmp_path, "decide", proposal, "--llm", f"cmd:{cmd}", "--timeout", "5", "--deadline", "60", "--parallel", "6"
    )
    assert proc.returncode == 1, proc.stderr
    pids = _sleeper_pids(pid_dir)
    assert len(pids) == 6  # every persona really started a child
    assert wait_until_dead(pids) == []


def test_children_are_dead_after_the_overall_deadline(proposal, tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    cmd = py_script(tmp_path, SLEEPER, "sleeper.py", pid_dir)
    proc = module_run(
        tmp_path, "decide", proposal, "--llm", f"cmd:{cmd}", "--timeout", "100", "--deadline", "4", "--parallel", "6"
    )
    assert proc.returncode == 1, proc.stderr
    pids = _sleeper_pids(pid_dir)
    assert len(pids) >= 4  # the persona calls that fit under the parallel cap
    assert wait_until_dead(pids) == []


# --- real process: python -m mirofish_lite -----------------------------------------------------------


def test_python_dash_m_exit_codes(proposal, tmp_path):
    assert module_run(tmp_path, "decide", proposal, "--llm", "mock").returncode == 0
    assert module_run(tmp_path, "decide", proposal, "--llm", "mock:risk-officer=NO,cash-flow=NO").returncode == 1
    assert (
        module_run(tmp_path, "decide", proposal, "--llm", "mock:risk-officer=ABSTAIN,cash-flow=ABSTAIN").returncode == 2
    )
    assert module_run(tmp_path, "decide", proposal).returncode == 3
    assert module_run(tmp_path, "decide").returncode == 3
    assert module_run(tmp_path).returncode == 3


def test_python_dash_m_board_prints_personas(tmp_path):
    proc = module_run(tmp_path, "board")
    assert proc.returncode == 0 and "newcomer" in proc.stdout
