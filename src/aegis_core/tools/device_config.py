from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

T = TypeVar("T", bound=BaseModel)
MAX_DEVICE_CONFIG_BYTES = 65_536


class DeviceConfigurationError(ValueError):
    """Raised when a local device profile file is absent or unsafe."""


def load_private_profile_list(path: Path, model: type[T]) -> tuple[T, ...]:
    """Load an owner-only, bounded JSON array without following symbolic links."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return ()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or not 1 <= info.st_size <= MAX_DEVICE_CONFIG_BYTES
    ):
        raise DeviceConfigurationError("device profile file must be owner-only and bounded")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise DeviceConfigurationError("device profile identity changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8_192, MAX_DEVICE_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DEVICE_CONFIG_BYTES:
                raise DeviceConfigurationError("device profile exceeds the safety ceiling")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        raw = json.loads(b"".join(chunks).decode("utf-8"))
        profiles = TypeAdapter(list[model]).validate_python(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
        raise DeviceConfigurationError("device profile JSON is invalid") from error
    identifiers = [
        str(getattr(profile, "device_id", getattr(profile, "shortcut_id", "")))
        for profile in profiles
    ]
    if len(identifiers) != len(set(identifiers)):
        raise DeviceConfigurationError("device profile identifiers must be unique")
    return tuple(profiles)
