#!/usr/bin/env python3
"""Record the actual remote Apple Silicon environment before qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

FORMAT = "jarvis-ci-macos-environment/1"
COMMANDS = {
    "os": ("/usr/bin/sw_vers", "-productVersion"),
    "architecture": ("/usr/bin/uname", "-m"),
    "xcode": ("/usr/bin/xcodebuild", "-version"),
    "sdk": ("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"),
    "swift": ("/usr/bin/xcrun", "swift", "--version"),
}
OUTPUT_LIMIT = 4096
RECEIPT_LIMIT = 65536


class EnvironmentCheckError(RuntimeError):
    """Failures expose stable codes, never tool stderr or environment values."""


def require(condition, code):
    if not condition:
        raise EnvironmentCheckError(code)


def capture(arguments, *, timeout=10):
    require(arguments in COMMANDS.values(), "command_not_admitted")
    deadline, output, completed = time.monotonic() + timeout, bytearray(), False
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, "command_timeout")
                for key, _ in selector.select(.1):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    require(len(output) + len(block) <= OUTPUT_LIMIT, "command_output_limit")
                    output.extend(block)
        code = process.wait(timeout=max(.01, deadline - time.monotonic()))
        completed = True
        require(code == 0, "command_failed")
        return output.decode("ascii").strip()
    finally:
        try:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)  # Only the new diagnostic group.
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
        finally:
            process.stdout.close()


def version(value):
    require(isinstance(value, str) and len(value) <= 32
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", value), "version_invalid")
    return tuple(map(int, value.split(".")))


def collect(environment=None, *, command_runner=capture):
    environment = os.environ if environment is None else environment
    require(sys.platform == "darwin" and environment.get("CI") == "true"
            and environment.get("GITHUB_ACTIONS") == "true", "remote_macos_ci_required")
    commit, run_id = environment.get("GITHUB_SHA", ""), environment.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[a-f0-9]{40}", commit)
            and re.fullmatch(r"[1-9][0-9]{0,19}", run_id), "ci_identity_invalid")
    values = {}
    for name, arguments in COMMANDS.items():
        value = command_runner(arguments, timeout=10)
        require(isinstance(value, str) and len(value.encode("utf-8")) <= OUTPUT_LIMIT,
                "command_output_limit")
        values[name] = value
    require(values["architecture"] == "arm64", "apple_silicon_required")
    require(version(values["os"]) >= (27, 0), "macos_27_required")
    require(version(values["sdk"]) >= (27, 0), "sdk_27_required")
    xcode = re.fullmatch(r"Xcode ([0-9]+(?:\.[0-9]+){1,2})\nBuild version ([A-Za-z0-9.]+)",
                         values["xcode"])
    swift = re.search(r"\b(?:Apple )?Swift version ([0-9]+(?:\.[0-9]+){1,2})\b",
                      values["swift"])
    target = re.search(r"(?m)^Target: (arm64-apple-macosx[0-9.]+)$", values["swift"])
    require(xcode and swift and target, "toolchain_version_invalid")
    return {"format": FORMAT, "source_commit": commit, "run_id": run_id,
            "os_version": values["os"], "architecture": values["architecture"],
            "sdk_version": values["sdk"], "xcode_version": xcode[1], "xcode_build": xcode[2],
            "swift_version": swift[1], "swift_target": target[1],
            "full_workflow_success_claimed": False}


def write_receipt(output: Path, receipt):
    payload = (json.dumps(receipt, sort_keys=True) + "\n").encode("ascii")
    require(len(payload) <= RECEIPT_LIMIT, "receipt_output_limit")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
    return payload.decode("ascii").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Comprobar el entorno remoto Apple Silicon")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(write_receipt(args.output, collect()))
    except (EnvironmentCheckError, OSError, subprocess.SubprocessError,
            UnicodeError, ValueError) as error:
        failure = {"format": FORMAT, "status": "rejected",
                   "error_class": type(error).__name__, "full_workflow_success_claimed": False}
        if isinstance(error, EnvironmentCheckError):
            failure["error_code"] = str(error)
        print(json.dumps(failure), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
