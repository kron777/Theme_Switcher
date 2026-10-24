"""
URL analysis: figure out what, if anything, a pasted URL points to in the
way of a downloadable theme archive. Never executes anything from the
target page — this only reads HTML/JSON text and looks for links.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin

from . import config
from . import downloader

GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)
GITHUB_RELEASES_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/releases/?(?:tag/(?P<tag>[\w.\-+]+))?/?$"
)
GITHUB_ASSET_RE = re.compile(
    r"^https?://github\.com/[\w.-]+/[\w.-]+/releases/download/"
)


def _kind_guess(name: str) -> str:
    n = name.lower()
    if "cursor" in n:
        return "cursor-theme"
    if "icon" in n:
        return "icon-theme"
    if any(w in n for w in ("wallpaper", "background")):
        return "wallpaper"
    if any(w in n for w in ("gtk", "shell", "theme")):
        return "gtk-or-shell-theme"
    return "unknown"


@dataclass
class AssetCandidate:
    url: str
    filename_hint: str
    kind_guess: str
    size_hint: int | None = None
    source: str = ""   # how we found it: direct-link / github-release / github-zipball / page-scan


@dataclass
class AnalysisResult:
    ok: bool
    project_name: str = ""
    source_site: str = ""
    candidates: list = field(default_factory=list)
    message: str = ""
    preview_images: list = field(default_factory=list)


def _is_archive_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(config.ALLOWED_ARCHIVE_EXT)


def analyze(url: str) -> AnalysisResult:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return AnalysisResult(ok=False, message="that doesn't look like a web address (needs http:// or https://)")

    if _is_archive_url(url):
        fname = parsed.path.rsplit("/", 1)[-1]
        return AnalysisResult(
            ok=True,
            project_name=fname,
            source_site=parsed.netloc,
            candidates=[AssetCandidate(url=url, filename_hint=fname,
                                        kind_guess=_kind_guess(fname), source="direct-link")],
            message="direct archive link detected",
        )

    if GITHUB_ASSET_RE.match(url):
        fname = parsed.path.rsplit("/", 1)[-1]
        if not fname.lower().endswith(config.ALLOWED_ARCHIVE_EXT):
            return AnalysisResult(ok=False, message=(
                "this GitHub release asset isn't a recognised archive type "
                f"({fname}) — only {', '.join(config.ALLOWED_ARCHIVE_EXT)} are supported"))
        return AnalysisResult(
            ok=True, project_name=fname, source_site="github.com",
            candidates=[AssetCandidate(url=url, filename_hint=fname,
                                        kind_guess=_kind_guess(fname), source="direct-link")],
            message="GitHub release asset detected",
        )

    m = GITHUB_RELEASES_RE.match(url)
    if m:
        return _analyze_github_releases(m.group("owner"), m.group("repo"), m.group("tag"))

    m = GITHUB_REPO_RE.match(url)
    if m:
        return _analyze_github_repo(m.group("owner"), m.group("repo"))

    return _analyze_generic_page(url)


def _analyze_github_releases(owner: str, repo: str, tag: str | None) -> AnalysisResult:
    api_url = (f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
               if tag else f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    text = downloader.fetch_text(api_url)
    if not text:
        return AnalysisResult(ok=False, message="couldn't reach the GitHub API for that release")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return AnalysisResult(ok=False, message="GitHub returned something unexpected for that release")

    assets = data.get("assets", [])
    candidates = [
        AssetCandidate(url=a["browser_download_url"], filename_hint=a["name"],
                        kind_guess=_kind_guess(a["name"]), size_hint=a.get("size"),
                        source="github-release")
        for a in assets if a.get("name", "").lower().endswith(config.ALLOWED_ARCHIVE_EXT)
    ]
    if not candidates:
        return AnalysisResult(
            ok=False, project_name=f"{owner}/{repo}",
            message="that release has no .zip/.tar.gz/.tar.xz/.tar.bz2 assets attached — "
                    "try the repository's main page instead, or supply a direct archive URL",
        )
    return AnalysisResult(ok=True, project_name=f"{owner}/{repo}", source_site="github.com",
                           candidates=candidates, message=f"{len(candidates)} archive asset(s) found")


def _analyze_github_repo(owner: str, repo: str) -> AnalysisResult:
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    text = downloader.fetch_text(api_url)
    if not text:
        return AnalysisResult(ok=False, message="couldn't reach the GitHub API for that repository")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return AnalysisResult(ok=False, message="GitHub returned something unexpected for that repository")
    if "message" in data and "default_branch" not in data:
        return AnalysisResult(ok=False, message=f"GitHub: {data.get('message', 'repository not found')}")

    branch = data.get("default_branch", "main")
    name = data.get("name", repo)
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"

    candidates = [AssetCandidate(url=zip_url, filename_hint=f"{name}-{branch}.zip",
                                  kind_guess="gtk-or-shell-theme", source="github-zipball")]

    # Also surface any tagged releases, since they're often the better source.
    rel = _analyze_github_releases(owner, repo, None)
    if rel.ok:
        candidates = rel.candidates + candidates

    return AnalysisResult(ok=True, project_name=f"{owner}/{repo}", source_site="github.com",
                           candidates=candidates,
                           message=f"repository found (default branch: {branch})")


_LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _analyze_generic_page(url: str) -> AnalysisResult:
    html = downloader.fetch_text(url)
    if html is None:
        return AnalysisResult(ok=False, message="couldn't reach that page")

    links = set(_LINK_RE.findall(html))
    archive_links = [urljoin(url, l) for l in links if l.lower().split("?")[0].endswith(config.ALLOWED_ARCHIVE_EXT)]
    candidates = [
        AssetCandidate(url=l, filename_hint=l.rsplit("/", 1)[-1],
                        kind_guess=_kind_guess(l), source="page-scan")
        for l in sorted(set(archive_links))
    ]

    images = [urljoin(url, u) for u in _IMG_RE.findall(html)
              if u.lower().split("?")[0].endswith(config.IMAGE_EXT)][:6]

    if not candidates:
        return AnalysisResult(
            ok=False, source_site=urlparse(url).netloc, preview_images=images,
            message=("couldn't find a downloadable theme archive on that page automatically. "
                     "If you know the direct link to a .zip/.tar.gz/.tar.xz/.tar.bz2 file, "
                     "paste that instead."),
        )

    return AnalysisResult(ok=True, source_site=urlparse(url).netloc, candidates=candidates,
                           preview_images=images,
                           message=f"{len(candidates)} archive link(s) found on the page")
