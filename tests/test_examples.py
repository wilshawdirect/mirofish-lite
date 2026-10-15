import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "name, expect", [("deploy_gate.py", ["APPROVE", "VETO"]), ("spend_gate.py", ["APPROVE", "VETO", "human sign-off"])]
)
def test_example_runs_offline_and_exits_zero(name, expect, tmp_path):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)  # the examples must find the package on their own
    proc = subprocess.run(
        [sys.executable, str(ROOT / "examples" / name)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for token in expect:
        assert token in proc.stdout
    assert "MOCK MODEL - NOT A REVIEW" in proc.stdout  # the examples are scripted demos, and say so
