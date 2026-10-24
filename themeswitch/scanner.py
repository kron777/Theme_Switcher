"""
Post-extraction content scanner — the last line of defense before a
downloaded theme's files get copied into place.

ThemeSwitch never executes anything from a download (by design, see
pipeline.py/installer.py), so a disguised executable sitting inert on
disk was never going to run *through this app*. But inert isn't the
same as harmless — it's still a payload sitting on the user's disk. This
scanner rejects the whole installation outright if anything turns up
that isn't a legitimate theme asset, verified by actual file content,
not just by extension (a renamed shell script can't hide behind a .png
extension here — the first bytes are checked regardless of what the
file is named).

A legitimate GTK/Shell/icon/cursor theme only ever needs: CSS, an
index.theme/settings.ini, images, and XCursor binary cursor files. If
an archive contains anything else — a script, a compiled binary, a
nested archive — that is not a theme being unusually creative, that is
not a theme.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field

TEXT_ASSET_EXTENSIONS = {
    ".css", ".theme", ".ini", ".txt", ".md", ".json", ".xml", ".rc",
}
IMAGE_EXTENSIONS = {
    ".png", ".svg", ".jpg", ".jpeg", ".gif", ".xpm", ".bmp", ".cur", ".ani",
}
ALLOWED_EXTENSIONS = TEXT_ASSET_EXTENSIONS | IMAGE_EXTENSIONS
ALLOWED_NO_EXT_NAMES = {"license", "readme", "copying", "authors", "changelog", "notice"}

# (magic bytes, human description) — checked against actual file content,
# independent of whatever extension the file is wearing.
DANGEROUS_MAGIC = [
    (b"\x7fELF", "an ELF executable"),
    (b"MZ", "a Windows PE/DOS executable"),
    (b"\xfe\xed\xfa\xce", "a Mach-O 32-bit executable"),
    (b"\xfe\xed\xfa\xcf", "a Mach-O 64-bit executable"),
    (b"\xce\xfa\xed\xfe", "a Mach-O 32-bit executable (byte-swapped)"),
    (b"\xcf\xfa\xed\xfe", "a Mach-O 64-bit executable (byte-swapped)"),
    (b"\xca\xfe\xba\xbe", "a Java class file / Mach-O fat binary"),
    (b"PK\x03\x04", "a nested zip/jar/apk archive"),
]

XCURSOR_MAGIC = b"Xcur"
SHEBANG_RE = re.compile(rb"^#!\s*/")

DISALLOWED_EXPLICIT_EXT = {
    ".sh", ".bash", ".zsh", ".py", ".pl", ".rb", ".php", ".exe", ".bat",
    ".cmd", ".ps1", ".vbs", ".scr", ".com", ".msi", ".deb", ".rpm",
    ".appimage", ".jar", ".so", ".dylib", ".dll", ".bin", ".run", ".elf",
    ".desktop",  # a .desktop file could itself be an Exec= launcher
}


@dataclass
class ScanFinding:
    path: str
    reason: str


@dataclass
class ScanResult:
    ok: bool
    findings: list = field(default_factory=list)
    files_scanned: int = 0


def _check_file(full: str, rel: str) -> ScanFinding | None:
    fn = os.path.basename(full)
    ext = os.path.splitext(fn)[1].lower()

    try:
        with open(full, "rb") as f:
            head = f.read(64)
    except OSError:
        return None

    # Content checks first — these catch a disguised file regardless of
    # what extension it's wearing.
    if SHEBANG_RE.match(head):
        return ScanFinding(rel, "executable script (shebang line) disguised as a theme file")

    for magic, desc in DANGEROUS_MAGIC:
        if head.startswith(magic):
            return ScanFinding(rel, f"{desc}, disguised with a {ext or 'no'}-extension name")

    # Extension-based checks, only for files that passed the content check.
    if ext in DISALLOWED_EXPLICIT_EXT:
        return ScanFinding(rel, f"disallowed file type ({ext}) — not a theme asset")

    if ext == "":
        if "cursors" in rel.split(os.sep):
            if not head.startswith(XCURSOR_MAGIC):
                return ScanFinding(rel, "unrecognised binary file inside a cursors/ directory")
            return None
        if fn.lower() not in ALLOWED_NO_EXT_NAMES:
            return ScanFinding(rel, "unrecognised file with no extension")
        return None

    if ext not in ALLOWED_EXTENSIONS:
        return ScanFinding(rel, f"unexpected file type ({ext}) — not a recognised theme asset")

    return None


def scan_directory(root: str) -> ScanResult:
    """Walk every real file under root (symlinks are skipped — they were
    already validated to stay inside the extraction root by archive.py,
    and following them here would just re-check the same target file
    reached a different way)."""
    findings = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            count += 1
            rel = os.path.relpath(full, root)
            finding = _check_file(full, rel)
            if finding:
                findings.append(finding)

    return ScanResult(ok=(len(findings) == 0), findings=findings, files_scanned=count)
