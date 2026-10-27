#!/usr/bin/env python3
"""Revisa una versión local; --launch abre el par app/código tras decisión explícita.

El pin debe exportarse desde una revisión actual del host. Este comando no crea
copias, no autentica el pin, no inicia sesión ni admite perfiles restaurados.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_core import local_release_update as core
from jarvis_core.host_profile import ProfileLease
from jarvis_core.local_release_launch import (
    read_closed_maintenance_journal,
    resolve_launch,
    validate_profile_for_launch,
)
from jarvis_core.recovery import RecoveryContext
from jarvis_core.recovery_fs import FileIdentity


def _context(pin):
    return RecoveryContext(
        uuid4().hex,
        pin.profile_id,
        pin.generation,
        lambda *_: None,
        threading.Event(),
        time.monotonic() + 120,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--profile-pin", type=Path, required=True)
    parser.add_argument("--expected-current-sha256", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path,
                        help="Directorio explícito de las dependencias Python cualificadas.")
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Ejecuta la app elegida; exige el perfil anterior cerrado.",
    )
    args = parser.parse_args(argv)
    owner = core.ReleaseOwner()
    lease = None
    retain_lease = False
    try:
        parent = core._Dir(args.profile_pin.parent, owner)
        if args.profile_pin.name.casefold() in core._FORBIDDEN:
            raise core.ReleaseError("release_profile_pin_invalid")
        raw, _ = core._read(parent, args.profile_pin.name, 16384, lambda: None)
        value = core._decode(raw)
        try:
            pin = core.ProfilePin(
                value["profile_id"],
                value["generation"],
                FileIdentity(
                    int(value["root_identity"]["device"]), int(value["root_identity"]["inode"])
                ),
                value["source_fingerprint"],
                tuple(sorted(value["schemas"].items())),
                value["backup_manifest_sha256"],
                tuple(value.get("maintenance_purposes", ())),
            )
        except (ValueError, TypeError, KeyError):
            raise core.ReleaseError("release_profile_pin_invalid") from None
        profile = core._Dir(args.state, owner, private=True)
        if profile.identity != pin.root_identity:
            raise core.ReleaseError("release_profile_pin_changed")
        # Even an admitted restored profile uses the separate native handoff,
        # which binds the owner's exact marker/admission and closes the old host.
        try:
            os.stat("restore_record.json", dir_fd=profile.fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise core.ReleaseError("release_restored_profile_requires_owner_handoff")
        root = core._Dir(args.install_root, owner, private=True)
        if args.launch:
            core._lock(root, owner)
        plan = resolve_launch(
            args.install_root,
            _context(pin),
            pin,
            expected_current_sha256=args.expected_current_sha256,
        )
        arguments = plan.arguments(python=args.python, codex=args.codex, state=args.state,
                                   dependencies=args.dependencies)
        if not args.launch:
            print(
                json.dumps(
                    {
                        **plan.metadata(),
                        "arguments": arguments,
                        "profile_content_checked_by_cli": False,
                        "requires_fresh_host_pin": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            owner.cleanup()
            return 0
        for executable in (args.python, args.codex):
            if (
                not executable.is_absolute()
                or not executable.is_file()
                or not os.access(executable, os.X_OK)
            ):
                raise core.ReleaseError("release_external_runtime_invalid")
        # Never terminate another service. Only a normally closed profile can
        # be opened, and the new service acquires its own lease on startup.
        lease = ProfileLease.acquire(args.state, create=False)
        journal = read_closed_maintenance_journal(args.state, _context(pin), lease)
        families = validate_profile_for_launch(
            args.state, _context(pin), pin, lease, maintenance_purposes=plan.maintenance_purposes
        )
        # Same installation lock, fresh payload/CURRENT read and unchanged cold
        # source families at the final handoff boundary, including WAL schemas.
        plan = resolve_launch(
            args.install_root,
            _context(pin),
            pin,
            expected_current_sha256=args.expected_current_sha256,
        )
        arguments = plan.arguments(python=args.python, codex=args.codex, state=args.state,
                                   dependencies=args.dependencies)
        environment = {
            key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ
        }
        environment["PYTHONNOUSERSITE"] = "1"
        lease.assert_current()
        for family in families:
            family.assert_current()
        if read_closed_maintenance_journal(args.state, _context(pin), lease) != journal:
            raise core.ReleaseError("release_launch_journal_changed")
        lease.close()
        lease = None
        # Direct exec, no shell or artifact-provided lifecycle hooks. Pinned
        # installation descriptors are CLOEXEC; no lock crosses into the app.
        os.execve(arguments[0], arguments, environment)
    except BaseException as error:
        pending = getattr(error, "owner", None)
        if lease is not None and pending is not None and not pending.closed:
            try:
                pending.cleanup()
            except BaseException:
                pass
            # If private capture cleanup is uncertain the failing CLI retains
            # the lease until process exit closes all kernel handles together.
            retain_lease = not pending.closed
            if retain_lease:
                error.retained_launch_lease = lease
        raise
    finally:
        if lease is not None and not retain_lease:
            lease.close()
        owner.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
