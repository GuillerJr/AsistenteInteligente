"""The remote smoke itself cannot accumulate an unbounded diagnostic stream."""

import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "jarvis_qualification_output", Path(__file__).resolve().parents[1]
    / "script/qualify_bundled_runtime.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_bounded_capture_drains_both_streams_and_reaps_success():
    code, output, errors = MODULE.bounded_capture(
        [sys.executable, "-I", "-c",
         "import sys; print('ready'); print('diagnostic',file=sys.stderr)"],
        environment={"PATH": "/usr/bin:/bin"}, timeout=5)
    assert code == 0 and output == b"ready\n" and errors == b"diagnostic\n"


@pytest.mark.parametrize("descriptor", [1, 2])
def test_output_limit_is_enforced_while_child_runs_and_child_is_reaped(descriptor):
    with pytest.raises(RuntimeError, match="qualification_output_limit"):
        MODULE.bounded_capture(
            [sys.executable, "-I", "-c",
             f"import os,time; os.write({descriptor},b'x'*65536); time.sleep(30)"],
            environment={"PATH": "/usr/bin:/bin"}, timeout=5,
            stdout_limit=1024, stderr_limit=1024)


def test_silent_child_cannot_outlive_qualification_deadline():
    with pytest.raises(RuntimeError, match="qualification_deadline"):
        MODULE.bounded_capture([sys.executable, "-I", "-c", "import time;time.sleep(30)"],
                               environment={"PATH": "/usr/bin:/bin"}, timeout=.2)
