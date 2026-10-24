"""
Safe archive extraction for untrusted downloads.

Threat model: the archive itself is attacker-controlled input. Guards against:
  - path traversal (entries escaping the extraction directory)
  - symlink/hardlink tricks (entries that are or point through links)
  - zip bombs (entry count and cumulative decompressed-size limits)
  - absolute-path entries
Nothing here ever shells out — extraction is pure Python stdlib, member by
member, each one individually validated before a single byte is written.
"""
from __future__ import annotations
import os
import tarfile
import zipfile
from dataclasses import dataclass, field

from . import config


class ArchiveError(Exception):
    pass


@dataclass
class ExtractResult:
    ok: bool
    extracted_files: list = field(default_factory=list)
    extracted_dirs: list = field(default_factory=list)
    error: str = ""


def _safe_target(base: str, member_name: str) -> str:
    """Resolve a member's target path and verify it stays inside base.
    Raises ArchiveError on any traversal attempt."""
    if member_name.startswith("/") or member_name.startswith("\\"):
        raise ArchiveError(f"rejected absolute path in archive: {member_name}")
    if ":" in member_name[:3]:  # e.g. C:\ on a malformed/cross-platform archive
        raise ArchiveError(f"rejected drive-letter path in archive: {member_name}")

    target = os.path.normpath(os.path.join(base, member_name))
    base_real = os.path.realpath(base)
    target_real = os.path.realpath(os.path.dirname(target))

    # The containing directory must resolve to somewhere under base.
    if os.path.commonpath([base_real, target_real]) != base_real:
        raise ArchiveError(f"path traversal rejected: {member_name}")
    return target


def _validate_symlink_target(base: str, member_name: str, link_target: str) -> str:
    """A symlink is only safe if, once resolved, it still points inside
    `base`. Archives legitimately use internal symlinks a lot (icon themes
    commonly share a subdirectory between variants instead of duplicating
    it) — the actual attack is a link that ESCAPES the extraction
    directory, not a link per se. Returns the validated (relative) target
    on success; raises ArchiveError otherwise."""
    if link_target.startswith("/") or link_target.startswith("\\"):
        raise ArchiveError(f"rejected symlink with absolute target: {member_name} -> {link_target}")
    if ":" in link_target[:3]:
        raise ArchiveError(f"rejected symlink with drive-letter target: {member_name} -> {link_target}")

    link_path = _safe_target(base, member_name)
    link_dir = os.path.dirname(link_path)
    resolved = os.path.normpath(os.path.join(link_dir, link_target))

    base_real = os.path.realpath(base)
    # The link's resolved location may not exist yet (sibling extracted
    # later in the archive), so normpath rather than realpath on the
    # target itself — but it must still textually resolve under base.
    if os.path.commonpath([base_real, os.path.normpath(resolved)]) != base_real:
        raise ArchiveError(f"rejected symlink escaping extraction dir: {member_name} -> {link_target}")
    return link_target


def extract_zip(archive_path: str, dest_dir: str) -> ExtractResult:
    os.makedirs(dest_dir, exist_ok=True)
    result = ExtractResult(ok=False)
    try:
        with zipfile.ZipFile(archive_path) as zf:
            infos = zf.infolist()
            if len(infos) > config.MAX_ARCHIVE_ENTRIES:
                return ExtractResult(ok=False, error="archive has too many entries")

            total = 0
            for info in infos:
                total += info.file_size
                if total > config.MAX_EXTRACTED_BYTES:
                    return ExtractResult(ok=False, error="archive exceeds extraction size limit")

            for info in infos:
                name = info.filename
                # zip stores symlinks via unix mode bits in external_attr;
                # the high 16 bits are the st_mode when made on unix.
                mode = (info.external_attr >> 16) & 0xFFFF
                is_symlink = bool(mode) and (mode & 0o170000) == 0o120000

                if is_symlink:
                    link_target = zf.read(info).decode("utf-8", errors="strict")
                    safe_target = _validate_symlink_target(dest_dir, name, link_target)
                    link_path = _safe_target(dest_dir, name)
                    os.makedirs(os.path.dirname(link_path), exist_ok=True)
                    if os.path.lexists(link_path):
                        os.remove(link_path)
                    os.symlink(safe_target, link_path)
                    result.extracted_files.append(link_path)
                    continue

                target = _safe_target(dest_dir, name)

                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    result.extracted_dirs.append(target)
                    continue

                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    remaining = config.MAX_EXTRACTED_BYTES
                    while True:
                        chunk = src.read(config.DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            dst.close()
                            os.remove(target)
                            raise ArchiveError("extraction size limit exceeded mid-stream")
                        dst.write(chunk)
                os.chmod(target, 0o644)  # never trust archive-supplied exec bits
                result.extracted_files.append(target)

        result.ok = True
        return result
    except ArchiveError as e:
        return ExtractResult(ok=False, error=str(e))
    except (zipfile.BadZipFile, OSError) as e:
        return ExtractResult(ok=False, error=f"invalid archive: {e}")


def extract_tar(archive_path: str, dest_dir: str, mode: str) -> ExtractResult:
    os.makedirs(dest_dir, exist_ok=True)
    try:
        with tarfile.open(archive_path, mode) as tf:
            members = tf.getmembers()
            if len(members) > config.MAX_ARCHIVE_ENTRIES:
                return ExtractResult(ok=False, error="archive has too many entries")

            total = sum(m.size for m in members if m.isfile())
            if total > config.MAX_EXTRACTED_BYTES:
                return ExtractResult(ok=False, error="archive exceeds extraction size limit")

            result = ExtractResult(ok=False)
            for m in members:
                if m.islnk():
                    # Hardlinks reference another archive member by name, not
                    # a filesystem path — different (and rarer, for themes)
                    # semantics than symlinks. Not worth the extra validation
                    # surface for something theme archives essentially never
                    # legitimately need.
                    raise ArchiveError(f"rejected hardlink entry: {m.name}")
                if m.isdev():
                    raise ArchiveError(f"rejected device-file entry: {m.name}")

                if m.issym():
                    safe_target = _validate_symlink_target(dest_dir, m.name, m.linkname)
                    link_path = _safe_target(dest_dir, m.name)
                    os.makedirs(os.path.dirname(link_path), exist_ok=True)
                    if os.path.lexists(link_path):
                        os.remove(link_path)
                    os.symlink(safe_target, link_path)
                    result.extracted_files.append(link_path)
                    continue

                target = _safe_target(dest_dir, m.name)

                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    result.extracted_dirs.append(target)
                    continue
                if not m.isfile():
                    continue  # skip anything else (fifo, etc.)

                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with src, open(target, "wb") as dst:
                    remaining = config.MAX_EXTRACTED_BYTES
                    while True:
                        chunk = src.read(config.DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            dst.close()
                            os.remove(target)
                            raise ArchiveError("extraction size limit exceeded mid-stream")
                        dst.write(chunk)
                os.chmod(target, 0o644)
                result.extracted_files.append(target)

        result.ok = True
        return result
    except ArchiveError as e:
        return ExtractResult(ok=False, error=str(e))
    except (tarfile.TarError, OSError) as e:
        return ExtractResult(ok=False, error=f"invalid archive: {e}")


def extract(archive_path: str, dest_dir: str) -> ExtractResult:
    lower = archive_path.lower()
    if lower.endswith(".zip"):
        return extract_zip(archive_path, dest_dir)
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return extract_tar(archive_path, dest_dir, "r:gz")
    if lower.endswith(".tar.xz"):
        return extract_tar(archive_path, dest_dir, "r:xz")
    if lower.endswith(".tar.bz2") or lower.endswith(".tbz2"):
        return extract_tar(archive_path, dest_dir, "r:bz2")
    return ExtractResult(ok=False, error=f"unsupported archive type: {archive_path}")
