"""Strict rollback recovery only on pinned private Jarvis SQLite copies.

The journal format is checked independently before SQLite can roll it back.
No super-journal path is followed. Inactive zero-length/zero-header journals
are explicitly identified and never applied. Original files remain untouched.
"""

from __future__ import annotations

import hashlib
import os
import struct
from uuid import uuid4

from jarvis_core import cold_wal

_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
_MASK = (1 << 32) - 1


class ColdJournalError(cold_wal.ColdWalError):
    pass


class JournalCaptureOwner(cold_wal.WalCaptureOwner):
    def __init__(self, family, maximum):
        super().__init__(family, maximum)
        self.name = "jarvis-cold-journal-" + uuid4().hex
        self.root_path = cold_wal._TEMP_ROOT / self.name


def _read_exact(fd, count, offset):
    raw = os.pread(fd, count, offset)
    if len(raw) != count:
        raise ColdJournalError("profile_cold_journal_truncated")
    return raw


def _zero_padding(fd, start, end, checkpoint):
    while start < end:
        checkpoint("cold_journal_validate")
        raw = _read_exact(fd, min(65536, end - start), start)
        if any(raw):
            raise ColdJournalError("profile_cold_journal_ambiguous_tail")
        start += len(raw)


def validate_journal(fd, size, main_header, main_bytes, limits, checkpoint):
    """Validate sector headers and sparse page checksums from SQLite's format.

    See sqlite.org/fileformat.html#the_rollback_journal. This parser never applies
    a page or guesses missing records: SQLite recovers the complete private copy.
    """
    page_size = cold_wal._main_page_size(main_header, main_bytes)
    if main_header[18:20] != b"\1\1":
        raise ColdJournalError("profile_cold_journal_invalid")
    if size > limits.max_sqlite_bytes or (size + 7) // 8 > limits.max_vm_steps:
        raise ColdJournalError("profile_cold_journal_size_limit")
    if size >= 16 and _read_exact(fd, 8, size - 8) == _MAGIC:
        # This is the native super-journal trailer sentinel. Never ask SQLite
        # to resolve the filename, even if the surrounding trailer is damaged.
        raise ColdJournalError("profile_cold_journal_super_journal_unsupported")
    if size == 0:
        return {"pages": main_bytes // page_size, "page_size": page_size,
                "headers": 0, "records": 0, "applied": False, "inactive_bytes": 0}
    first = _read_exact(fd, min(28, size), 0)
    if len(first) != 28:
        raise ColdJournalError("profile_cold_journal_truncated")
    if first == bytes(28):
        return {"pages": main_bytes // page_size, "page_size": page_size,
                "headers": 0, "records": 0, "applied": False, "inactive_bytes": size}
    offset, records, headers, synced_headers = 0, 0, 0, 0
    expected_sector, original_pages, seen, unsynced_records = None, None, set(), 0
    while offset < size:
        checkpoint("cold_journal_validate")
        if size - offset < 28:
            _zero_padding(fd, offset, size, checkpoint)
            break
        header = _read_exact(fd, 28, offset)
        if header == bytes(28):
            _zero_padding(fd, offset, size, checkpoint)
            break
        unsynced = header[:12] == bytes(12)
        if header[:8] != _MAGIC and not unsynced:
            raise ColdJournalError("profile_cold_journal_invalid")
        count, nonce, pages, sector, encoded_page = struct.unpack(">5I", header[8:])
        actual_page = encoded_page or page_size  # SQLite's legacy header uses the main page size.
        if (actual_page != page_size or sector < 512 or sector > 65536
                or sector & (sector - 1) or offset % sector
                or (expected_sector is not None and sector != expected_sector)
                or (original_pages is not None and pages != original_pages)):
            raise ColdJournalError("profile_cold_journal_invalid")
        if pages == 0:
            raise ColdJournalError("profile_cold_journal_empty_origin_unavailable")
        if pages * page_size > limits.max_sqlite_bytes:
            raise ColdJournalError("profile_cold_journal_size_limit")
        expected_sector, original_pages = sector, pages
        end_header = offset + sector
        if end_header > size:
            raise ColdJournalError("profile_cold_journal_truncated")
        _zero_padding(fd, offset + 28, end_header, checkpoint)
        headers += 1
        record_size = page_size + 8
        if unsynced or count == _MASK:
            count, remainder = divmod(size - end_header, record_size)
            if remainder:
                raise ColdJournalError("profile_cold_journal_truncated")
        if records + count > limits.max_rows:
            raise ColdJournalError("profile_cold_journal_work_limit")
        end = end_header + count * record_size
        if end > size:
            raise ColdJournalError("profile_cold_journal_truncated")
        for index in range(count):
            checkpoint("cold_journal_validate")
            raw = _read_exact(fd, record_size, end_header + index * record_size)
            page = int.from_bytes(raw[:4], "big")
            if page < 1 or page > pages or page in seen:
                raise ColdJournalError("profile_cold_journal_page_invalid")
            seen.add(page)
            data = memoryview(raw)[4:4 + page_size]
            checksum = nonce
            for position in range(page_size - 200, -1, -200):
                checksum = (checksum + data[position]) & _MASK
            if checksum != int.from_bytes(raw[-4:], "big"):
                raise ColdJournalError("profile_cold_journal_checksum_invalid")
        records += count
        if unsynced:
            # SQLite leaves magic/nRec unset until this segment is synced. Its
            # complete, checksum-verified records cannot authorize source page
            # writes and are not applied by the native hot-journal reader.
            unsynced_records += count
        else:
            synced_headers += 1
        if end == size:
            break
        aligned = (end + sector - 1) // sector * sector
        _zero_padding(fd, end, min(aligned, size), checkpoint)
        if aligned >= size:
            break
        offset = aligned
        if not count:
            # Zero records followed by another segment cannot advance native
            # recovery unambiguously; do not loop or infer missing records.
            raise ColdJournalError("profile_cold_journal_ambiguous_tail")
    if original_pages is None:
        raise ColdJournalError("profile_cold_journal_invalid")
    return {"pages": original_pages, "page_size": page_size, "headers": headers,
            "records": records, "applied": bool(synced_headers),
            "unsynced_records_not_applied": unsynced_records,
            "inactive_bytes": 0 if synced_headers else size}


def capture_journal(family, context, limits, check_current, *, inspect_image=True):
    owner = JournalCaptureOwner(family, limits.max_total_bytes)

    def capture():
        if not family.journal_eligible:
            raise ColdJournalError("profile_cold_journal_unavailable")
        checkpoint, hashes = cold_wal.acquire_family(
            owner, context, limits, check_current, {"", "-journal"})
        metadata = validate_journal(owner.stage_handles["database.sqlite3-journal"].fd,
                                    family.members[3][5], family.header, family.members[0][5],
                                    limits, checkpoint)
        data = cold_wal.native_image(owner, limits, checkpoint, pages=metadata["pages"],
                                     page_size=metadata["page_size"], wal=False,
                                     writable=True, inspect_image=inspect_image)
        cold_wal.finalize_family(owner, hashes, checkpoint)
        metadata.update(source_fingerprint=family.fingerprint,
                        source_member_sha256={suffix or "main": value.hex()
                                              for suffix, value in hashes.items()})
        return data, hashlib.sha256(data).hexdigest(), metadata

    try:
        return cold_wal.run_owned_capture(owner, capture)
    except cold_wal.ColdWalError as error:
        raise ColdJournalError(error.code.replace("profile_cold_wal_", "profile_cold_journal_"),
                               error.owner, cleanup_error_code=error.cleanup_error_code) from None


def read_journal_image(family, context, limits, check_current):
    data, digest, _ = capture_journal(family, context, limits, check_current)
    return data, digest
