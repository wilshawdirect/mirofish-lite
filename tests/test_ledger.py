import json
import threading

import pytest
from helpers import board, simple_personas

import mirofish_lite.ledger as ledger_module
from mirofish_lite import Board, Ledger, LedgerError, MockLLM, reply

PROPOSAL = "Delete the unused staging database after the 30-day hold."


def decision(text=PROPOSAL, responses=None, **kw):
    b, _ = board(responses, **kw)
    return b.decide(text)


def raw_lines(path):
    return [ln for ln in path.read_bytes().split(b"\n") if ln.strip()]


def test_append_writes_one_valid_json_line(tmp_path):
    ledger = Ledger(tmp_path / "d.jsonl")
    ledger.append(decision(), PROPOSAL)
    lines = raw_lines(tmp_path / "d.jsonl")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "decision" and rec["outcome"] == "APPROVE"
    assert len(rec["votes"]) == 5
    assert all("duration_s" in v for v in rec["votes"])
    assert rec["calls"] == 5 and "proposal_hash" in rec and "tally" in rec and "quorum_rule" in rec


def test_file_is_plain_ascii_with_unix_newlines(tmp_path):
    ledger = Ledger(tmp_path / "d.jsonl")
    d = decision(responses={"p0": reply("NO", "café — résumé 中文 \U0001f600")})
    ledger.append(d)
    data = (tmp_path / "d.jsonl").read_bytes()
    data.decode("ascii")
    assert data.endswith(b"\n") and b"\r" not in data
    assert list(ledger.read())[0]["dissent"][0]["reason"] == "café — résumé 中文 \U0001f600"


def test_appends_accumulate_in_order(tmp_path):
    ledger = Ledger(tmp_path / "d.jsonl")
    hashes = []
    for i in range(5):
        d = decision(f"proposal {i}")
        hashes.append(d.proposal_hash)
        ledger.append(d)
    assert [r["proposal_hash"] for r in ledger.read()] == hashes
    assert ledger.count() == 5


def test_a_second_ledger_object_appends_instead_of_truncating(tmp_path):
    path = tmp_path / "d.jsonl"
    Ledger(path).append(decision("one"))
    Ledger(path).append(decision("two"))
    assert Ledger(path).count() == 2


def test_the_file_only_ever_grows(tmp_path):
    path = tmp_path / "d.jsonl"
    ledger = Ledger(path)
    sizes = []
    for i in range(4):
        ledger.append(decision(f"p{i}"))
        sizes.append(path.stat().st_size)
    assert sizes == sorted(set(sizes))


def test_public_api_has_no_edit_or_delete(tmp_path):
    public = {n for n in dir(Ledger) if not n.startswith("_")}
    assert public == {"append", "archive", "count", "read"}


def test_proposal_text_is_not_stored_by_default_only_its_hash(tmp_path):
    path = tmp_path / "d.jsonl"
    d = decision()
    Ledger(path).append(d, PROPOSAL)
    text = path.read_text()
    assert PROPOSAL not in text and "Delete the unused" not in text
    assert d.proposal_hash in text


def test_proposal_text_is_stored_when_the_user_opts_in(tmp_path):
    path = tmp_path / "d.jsonl"
    Ledger(path, store_proposal=True).append(decision(), PROPOSAL)
    assert list(Ledger(path).read())[0]["proposal"] == PROPOSAL


def test_raw_reply_excerpts_follow_the_same_opt_in(tmp_path):
    d = decision(responses={"p0": "SECRET-LOOKING GARBAGE from the model"})
    assert d.parse_failures[0].excerpt.startswith("SECRET")
    off, on = tmp_path / "off.jsonl", tmp_path / "on.jsonl"
    Ledger(off).append(d, PROPOSAL)
    Ledger(on, store_proposal=True).append(d, PROPOSAL)
    assert "SECRET" not in off.read_text()
    assert "SECRET" in on.read_text()
    assert list(Ledger(off).read())[0]["parse_failures"][0]["error"]  # the error text is still kept


def test_parser_failures_and_errors_and_durations_are_recorded(tmp_path):
    d = decision(responses={"p0": "garbage", "p1": RuntimeError("down")})
    Ledger(tmp_path / "d.jsonl").append(d)
    rec = next(Ledger(tmp_path / "d.jsonl").read())
    assert rec["parse_failures"][0]["persona"] == "p0"
    assert rec["errors"][0]["persona"] == "p1"
    assert {v["status"] for v in rec["votes"]} == {"ok", "parse_failure", "error"}


def test_read_flags_corrupt_lines_instead_of_hiding_them(tmp_path):
    path = tmp_path / "d.jsonl"
    ledger = Ledger(path)
    ledger.append(decision("a"))
    with open(path, "ab") as f:
        f.write(b"this is not json\n")
    ledger.append(decision("b"))
    records = list(ledger.read())
    assert [r["type"] for r in records] == ["decision", "corrupt", "decision"]
    assert records[1]["line_no"] == 2


def test_read_of_a_missing_file_is_empty(tmp_path):
    assert list(Ledger(tmp_path / "nope.jsonl").read()) == []


def test_concurrent_appends_never_interleave(tmp_path):
    path = tmp_path / "d.jsonl"
    ledger = Ledger(path, fsync=False)
    d = decision()

    def writer():
        for _ in range(25):
            ledger.append(d)

    threads = [threading.Thread(target=writer) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    lines = raw_lines(path)
    assert len(lines) == 200
    assert all(json.loads(ln)["type"] == "decision" for ln in lines)


# --- archive -------------------------------------------------------------------


def fill(ledger, n):
    for i in range(n):
        ledger.append(decision(f"proposal {i}"))


def test_archive_moves_every_record_and_leaves_a_fresh_live_file(tmp_path):
    path = tmp_path / "decisions.jsonl"
    ledger = Ledger(path)
    fill(ledger, 5)
    before = path.read_bytes()
    result = ledger.archive()
    assert result.records == 5
    assert result.path.read_bytes() == before  # byte-identical
    assert result.path.name.startswith("decisions.") and result.path.suffix == ".jsonl"
    assert path.exists() and path.stat().st_size == 0
    assert ledger.count() == 0
    assert not list(tmp_path.glob("*.rotating"))


def test_writes_after_archive_land_in_the_live_file_not_the_archive(tmp_path):
    ledger = Ledger(tmp_path / "decisions.jsonl")
    fill(ledger, 3)
    result = ledger.archive()
    fill(ledger, 2)
    assert ledger.count() == 2
    assert Ledger(result.path).count() == 3


def test_archive_reports_a_verifiable_hash(tmp_path):
    import hashlib

    ledger = Ledger(tmp_path / "decisions.jsonl")
    fill(ledger, 2)
    result = ledger.archive()
    assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.sha256
    assert result.bytes == result.path.stat().st_size


def test_two_archives_never_overwrite_each_other(tmp_path):
    ledger = Ledger(tmp_path / "decisions.jsonl")
    fill(ledger, 2)
    first = ledger.archive()
    fill(ledger, 2)
    second = ledger.archive()
    assert first.path != second.path and first.path.exists() and second.path.exists()
    assert Ledger(first.path).count() == Ledger(second.path).count() == 2


def test_archive_can_target_another_directory(tmp_path):
    ledger = Ledger(tmp_path / "live" / "decisions.jsonl")
    fill(ledger, 3)
    result = ledger.archive(archive_dir=tmp_path / "cold" / "store")
    assert result.path.parent == tmp_path / "cold" / "store"
    assert Ledger(result.path).count() == 3


def test_archiving_nothing_returns_none_and_creates_nothing(tmp_path):
    ledger = Ledger(tmp_path / "decisions.jsonl")
    assert ledger.archive() is None
    ledger.path.touch()
    assert ledger.archive() is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["decisions.jsonl"]


def test_failed_verification_keeps_every_record(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    ledger = Ledger(path)
    fill(ledger, 4)
    original = path.read_bytes()

    def corrupt_copy(src, dst, **kw):
        with open(src, "rb") as fh:
            data = fh.read()
        with open(dst, "wb") as f:
            f.write(data[: len(data) // 2])  # a truncated copy
        return dst

    monkeypatch.setattr(ledger_module.shutil, "copyfile", corrupt_copy)
    with pytest.raises(LedgerError, match="did not verify"):
        ledger.archive()
    kept = list(tmp_path.glob("*.unverified"))
    assert len(kept) == 1 and kept[0].read_bytes() == original  # nothing lost
    assert list(tmp_path.glob("*.failed-verify"))  # the bad copy is set aside, not deleted
    assert not list(tmp_path.glob("*.rotating"))
    fill(ledger, 1)  # and the ledger keeps working
    assert ledger.count() == 1


def test_a_failing_copy_keeps_the_original(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    ledger = Ledger(path)
    fill(ledger, 3)
    original = path.read_bytes()

    def boom(src, dst, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(ledger_module.shutil, "copyfile", boom)
    with pytest.raises(LedgerError, match="disk full"):
        ledger.archive()
    kept = list(tmp_path.glob("*.unverified"))
    assert len(kept) == 1 and kept[0].read_bytes() == original


def test_a_failing_rename_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    ledger = Ledger(path)
    fill(ledger, 2)
    original = path.read_bytes()

    def no_replace(src, dst):
        raise PermissionError("file is in use")

    monkeypatch.setattr(ledger_module.os, "replace", no_replace)
    with pytest.raises(LedgerError, match="nothing was changed"):
        ledger.archive()
    assert path.read_bytes() == original


def test_no_record_is_lost_when_appending_during_archive(tmp_path):
    path = tmp_path / "decisions.jsonl"
    ledger = Ledger(path, fsync=False)
    d = decision()
    total = 300
    archives = []
    stop = threading.Event()

    def writer():
        for _ in range(total):
            ledger.append(d)
        stop.set()

    t = threading.Thread(target=writer)
    t.start()
    while not stop.is_set():
        r = ledger.archive()
        if r:
            archives.append(r)
    t.join()
    r = ledger.archive()
    if r:
        archives.append(r)
    archived = sum(Ledger(a.path).count() for a in archives)
    assert archived + ledger.count() == total


def test_a_ledger_can_be_used_by_a_board_end_to_end(tmp_path):
    ledger = Ledger(tmp_path / "decisions.jsonl")
    b = Board(simple_personas(5), MockLLM(), quorum=4, ledger=ledger)
    for i in range(3):
        b.decide(f"proposal {i}")
    assert ledger.archive().records == 3
