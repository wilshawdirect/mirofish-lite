"""Several processes appending to one ledger, and a ledger whose last line was torn."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mirofish_lite import Ledger

ROOT = Path(__file__).resolve().parent.parent

WRITER = """
import sys
from mirofish_lite import Ledger

path, count, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ledger = Ledger(path, fsync=False)
record = {"outcome": "APPROVE", "tag": tag, "pad": "x" * 6000}  # big enough to tear without a lock
for i in range(count):
    ledger.append({**record, "i": i})
"""


def run_writers(path, procs, count, tmp_path):
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    running = [
        subprocess.Popen(
            [sys.executable, str(script), str(path), str(count), f"proc{n}"], env=env, stderr=subprocess.PIPE
        )
        for n in range(procs)
    ]
    for proc in running:
        _, err = proc.communicate(timeout=240)
        assert proc.returncode == 0, err.decode(errors="replace")


@pytest.mark.parametrize("procs, count", [(4, 100), (6, 60)])
def test_several_processes_append_without_losing_or_tearing_records(tmp_path, procs, count):
    path = tmp_path / "shared.jsonl"
    run_writers(path, procs, count, tmp_path)
    records = list(Ledger(path).read())
    assert [r for r in records if r.get("type") == "corrupt"] == []
    assert len(records) == procs * count
    per_proc = {}
    for r in records:
        per_proc.setdefault(r["tag"], []).append(r["i"])
    assert sorted(per_proc) == [f"proc{n}" for n in range(procs)]
    assert all(seq == list(range(count)) for seq in per_proc.values())  # every record, in per-process order


def test_the_lock_sidecar_is_created_next_to_the_ledger(tmp_path):
    ledger = Ledger(tmp_path / "d.jsonl")
    ledger.append({"outcome": "APPROVE"})
    assert (tmp_path / "d.jsonl.lock").exists()


def test_a_torn_last_line_does_not_corrupt_the_next_record(tmp_path):
    path = tmp_path / "d.jsonl"
    ledger = Ledger(path)
    ledger.append({"outcome": "APPROVE", "n": 1})
    with open(path, "ab") as f:  # a crash mid-write: half a record, no trailing newline
        f.write(b'{"type":"decision","outcome":"VE')
    ledger.append({"outcome": "VETO", "n": 2})
    ledger.append({"outcome": "NO_QUORUM", "n": 3})

    records = list(ledger.read())
    assert [r["type"] for r in records] == ["decision", "corrupt", "decision", "decision"]
    assert [r.get("n") for r in records if r["type"] == "decision"] == [1, 2, 3]
    assert records[1]["raw"].startswith('{"type":"decision","outcome":"VE')  # the fragment is kept, not hidden
    # the new records are whole, valid JSON lines
    lines = path.read_bytes().split(b"\n")
    assert json.loads(lines[2])["n"] == 2 and json.loads(lines[3])["n"] == 3


def test_an_intact_file_gets_no_extra_blank_lines(tmp_path):
    path = tmp_path / "d.jsonl"
    ledger = Ledger(path)
    for n in range(3):
        ledger.append({"n": n})
    assert path.read_bytes().count(b"\n") == 3
    assert b"\n\n" not in path.read_bytes()


def test_archive_and_append_from_other_processes_do_not_lose_records(tmp_path):
    path = tmp_path / "shared.jsonl"
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [
        subprocess.Popen([sys.executable, str(script), str(path), "60", f"proc{n}"], env=env, stderr=subprocess.PIPE)
        for n in range(3)
    ]
    ledger = Ledger(path, fsync=False)
    archived = 0
    while any(p.poll() is None for p in procs):
        result = ledger.archive()
        if result:
            archived += Ledger(result.path).count()
    for p in procs:
        _, err = p.communicate(timeout=240)
        assert p.returncode == 0, err.decode(errors="replace")
    result = ledger.archive()
    if result:
        archived += Ledger(result.path).count()
    assert archived + ledger.count() == 3 * 60
