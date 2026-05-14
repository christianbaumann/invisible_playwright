import hashlib
import io
import tarfile
from pathlib import Path

import pytest
import responses

from invisible_playwright.constants import BINARY_VERSION
from invisible_playwright.download import (
    _extract,
    _github_token,
    _parse_checksums,
    _parse_owner_repo,
    _sha256_file,
    ensure_binary,
)


def _make_zip(path: Path, inner_name: str, payload: bytes) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, payload)
    data = buf.getvalue()
    path.write_bytes(data)
    return data


def _make_targz(path: Path, inner_name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=inner_name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    data = buf.getvalue()
    path.write_bytes(data)
    return data


@pytest.mark.unit
@responses.activate
def test_ensure_binary_downloads_and_verifies(tmp_path, monkeypatch):
    """Full path: cache miss -> HTTP GET -> SHA256 check -> extract -> return path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.zip"
    archive_bytes = _make_zip(archive_path, "firefox.exe", b"PEX!")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("win32", "AMD64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200,
                  content_type="application/zip")
    responses.add(responses.GET, url_sums,
                  body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    path = ensure_binary()
    assert Path(path).exists()
    assert Path(path).name == "firefox.exe"


@pytest.mark.unit
@responses.activate
def test_ensure_binary_rejects_sha_mismatch(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    archive_path = tmp_path / "archive.zip"
    archive_bytes = _make_zip(archive_path, "firefox.exe", b"PEX!")
    wrong_sha = "0" * 64
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("win32", "AMD64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"
    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{wrong_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    with pytest.raises(RuntimeError, match="SHA256"):
        ensure_binary()


# DL1: cache hit returns cached path without HTTP call
@pytest.mark.unit
def test_ensure_binary_cache_hit_skips_http(tmp_path, monkeypatch):
    """When the binary already exists in cache, ensure_binary returns immediately
    without issuing any HTTP request."""
    cache = tmp_path / "cache"
    version_dir = cache / BINARY_VERSION
    version_dir.mkdir(parents=True)
    pre_cached = version_dir / "firefox.exe"
    pre_cached.write_text("cached-content")

    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    def _fail_get(*args, **kwargs):
        raise AssertionError("HTTP must not be called on cache hit")
    monkeypatch.setattr("invisible_playwright.download.requests.get", _fail_get)

    path = ensure_binary()
    assert path == pre_cached
    assert path.read_text() == "cached-content"


# DL2: .tar.gz extraction works
@pytest.mark.unit
def test_extract_tar_gz(tmp_path):
    """_extract handles .tar.gz archives and unpacks the inner files."""
    archive = tmp_path / "bundle.tar.gz"
    _make_targz(archive, "firefox", b"ELF!")
    dst = tmp_path / "out"

    _extract(archive, dst)

    assert (dst / "firefox").exists()
    assert (dst / "firefox").read_bytes() == b"ELF!"


# DL3: checksum line with comment (#) is skipped
@pytest.mark.unit
def test_parse_checksums_skips_comments_and_blanks():
    text = (
        "# this is a comment\n"
        "\n"
        "   # indented comment\n"
        "abc123  file1.zip\n"
        "def456  file2.tar.gz\n"
    )
    out = _parse_checksums(text)
    assert out == {"file1.zip": "abc123", "file2.tar.gz": "def456"}


# DL3 sibling: malformed lines (fewer than 2 fields) are silently ignored
@pytest.mark.unit
def test_parse_checksums_ignores_single_field_lines():
    text = "loner\nabc123  file.zip\n"
    out = _parse_checksums(text)
    assert out == {"file.zip": "abc123"}


# DL3 sibling: last field is treated as filename (supports trailing whitespace tokens)
@pytest.mark.unit
def test_parse_checksums_uses_last_token_as_filename():
    text = "abc123  some/nested/file.zip\n"
    out = _parse_checksums(text)
    assert "some/nested/file.zip" in out


# DL4: unknown archive format (.rar) raises RuntimeError
@pytest.mark.unit
def test_extract_unknown_format_raises(tmp_path):
    archive = tmp_path / "thing.rar"
    archive.write_bytes(b"not-a-real-rar")
    dst = tmp_path / "out"

    with pytest.raises(RuntimeError, match="unknown archive format"):
        _extract(archive, dst)


# DL5: binary not found after extraction raises RuntimeError
@pytest.mark.unit
@responses.activate
def test_ensure_binary_missing_entry_after_extract_raises(tmp_path, monkeypatch):
    """If the archive extracts cleanly but the expected entry isn't present,
    ensure_binary raises RuntimeError."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.zip"
    # zip without firefox.exe inside
    archive_bytes = _make_zip(archive_path, "other.bin", b"X")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("win32", "AMD64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "win32")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    with pytest.raises(RuntimeError, match="binary not found after extraction"):
        ensure_binary()


# Pure helper: _parse_owner_repo
@pytest.mark.unit
def test_parse_owner_repo_valid():
    owner, repo = _parse_owner_repo(
        "https://github.com/feder-cr/invisible_playwright/releases/download/x/y"
    )
    assert owner == "feder-cr"
    assert repo == "invisible_playwright"


@pytest.mark.unit
def test_parse_owner_repo_invalid_raises():
    with pytest.raises(RuntimeError, match="cannot parse owner/repo"):
        _parse_owner_repo("not-a-github-url")


# Pure helper: _sha256_file matches hashlib output
@pytest.mark.unit
def test_sha256_file_matches_hashlib(tmp_path):
    payload = b"hello world"
    f = tmp_path / "file.bin"
    f.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _sha256_file(f) == expected


# _github_token precedence: STEALTHFOX_GITHUB_TOKEN beats GITHUB_TOKEN
@pytest.mark.unit
def test_github_token_stealthfox_wins(monkeypatch):
    monkeypatch.setenv("STEALTHFOX_GITHUB_TOKEN", "stealth")
    monkeypatch.setenv("GITHUB_TOKEN", "generic")
    assert _github_token() == "stealth"


@pytest.mark.unit
def test_github_token_falls_back_to_github_token(monkeypatch):
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "generic")
    assert _github_token() == "generic"


@pytest.mark.unit
def test_github_token_none_when_unset(monkeypatch):
    monkeypatch.delenv("STEALTHFOX_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _github_token() is None


# Bonus coverage: unsupported platform raises NotImplementedError before any HTTP
@pytest.mark.unit
def test_ensure_binary_unsupported_platform_raises(monkeypatch):
    monkeypatch.setattr("sys.platform", "freebsd")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    with pytest.raises(NotImplementedError, match="unsupported platform"):
        ensure_binary()


# ──────────────────────────────────────────────────────────────────────
#  Linux platform tests — exercise the tar.gz extraction path. Mirrors
#  the Windows .zip tests above so both archive formats are covered.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@responses.activate
def test_ensure_binary_downloads_and_verifies_linux(tmp_path, monkeypatch):
    """Linux happy path: tar.gz download → SHA256 check → extract → return path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.tar.gz"
    archive_bytes = _make_targz(archive_path, "firefox", b"ELF!")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("linux", "x86_64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200,
                  content_type="application/gzip")
    responses.add(responses.GET, url_sums,
                  body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "linux")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    path = ensure_binary()
    assert Path(path).exists()
    assert Path(path).name == "firefox"


@pytest.mark.unit
@responses.activate
def test_ensure_binary_rejects_sha_mismatch_linux(tmp_path, monkeypatch):
    """Linux SHA mismatch must raise — the tar.gz path runs the same
    verifier as the .zip path, so a corrupted archive is rejected before
    extraction regardless of platform."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    archive_path = tmp_path / "archive.tar.gz"
    archive_bytes = _make_targz(archive_path, "firefox", b"ELF!")
    wrong_sha = "0" * 64
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("linux", "x86_64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"
    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{wrong_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "linux")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="SHA256"):
        ensure_binary()


@pytest.mark.unit
def test_ensure_binary_cache_hit_skips_http_linux(tmp_path, monkeypatch):
    """Linux cache hit short-circuits before any HTTP. Looks for the
    ``firefox`` entry (not ``firefox.exe``) per ``BINARY_ENTRY_REL``."""
    cache = tmp_path / "cache"
    version_dir = cache / BINARY_VERSION
    version_dir.mkdir(parents=True)
    pre_cached = version_dir / "firefox"
    pre_cached.write_text("cached-content")

    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    monkeypatch.setattr("sys.platform", "linux")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    def _fail_get(*args, **kwargs):
        raise AssertionError("HTTP must not be called on cache hit")
    monkeypatch.setattr("invisible_playwright.download.requests.get", _fail_get)

    path = ensure_binary()
    assert path == pre_cached
    assert path.read_text() == "cached-content"


@pytest.mark.unit
@responses.activate
def test_ensure_binary_missing_entry_after_extract_raises_linux(tmp_path, monkeypatch):
    """Linux post-extract sanity check: if the tar.gz lacks a ``firefox``
    entry, raise rather than returning a non-existent path. Mirrors the
    Windows test and guards against an upstream release artifact regression."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.tar.gz"
    # tar.gz without ``firefox`` inside
    archive_bytes = _make_targz(archive_path, "other.bin", b"X")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("linux", "x86_64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "linux")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="binary not found after extraction"):
        ensure_binary()


# ──────────────────────────────────────────────────────────────────────
#  macOS platform tests — exercise the tar.gz extraction path with the
#  .app bundle binary entry (Firefox.app/Contents/MacOS/firefox).
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@responses.activate
def test_ensure_binary_downloads_and_verifies_macos(tmp_path, monkeypatch):
    """macOS happy path: tar.gz download → SHA256 check → extract → return path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.tar.gz"
    entry = "Firefox.app/Contents/MacOS/firefox"
    archive_bytes = _make_targz(archive_path, entry, b"MACHO!")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("darwin", "arm64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200,
                  content_type="application/gzip")
    responses.add(responses.GET, url_sums,
                  body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "darwin")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    path = ensure_binary()
    assert Path(path).exists()
    assert Path(path).name == "firefox"


@pytest.mark.unit
@responses.activate
def test_ensure_binary_rejects_sha_mismatch_macos(tmp_path, monkeypatch):
    """macOS SHA mismatch must raise before extraction."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    archive_path = tmp_path / "archive.tar.gz"
    entry = "Firefox.app/Contents/MacOS/firefox"
    archive_bytes = _make_targz(archive_path, entry, b"MACHO!")
    wrong_sha = "0" * 64
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("darwin", "arm64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"
    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{wrong_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "darwin")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    with pytest.raises(RuntimeError, match="SHA256"):
        ensure_binary()


@pytest.mark.unit
def test_ensure_binary_cache_hit_skips_http_macos(tmp_path, monkeypatch):
    """macOS cache hit short-circuits before any HTTP. Looks for the
    .app bundle entry per BINARY_ENTRY_REL."""
    cache = tmp_path / "cache"
    version_dir = cache / BINARY_VERSION
    entry_dir = version_dir / "Firefox.app" / "Contents" / "MacOS"
    entry_dir.mkdir(parents=True)
    pre_cached = entry_dir / "firefox"
    pre_cached.write_text("cached-content")

    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)
    monkeypatch.setattr("sys.platform", "darwin")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    def _fail_get(*args, **kwargs):
        raise AssertionError("HTTP must not be called on cache hit")
    monkeypatch.setattr("invisible_playwright.download.requests.get", _fail_get)

    path = ensure_binary()
    assert path == pre_cached
    assert path.read_text() == "cached-content"


@pytest.mark.unit
@responses.activate
def test_ensure_binary_missing_entry_after_extract_raises_macos(tmp_path, monkeypatch):
    """macOS post-extract sanity check: if the tar.gz lacks the .app bundle
    entry, raise rather than returning a non-existent path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("invisible_playwright.download.cache_root", lambda: cache)

    archive_path = tmp_path / "archive.tar.gz"
    archive_bytes = _make_targz(archive_path, "other.bin", b"X")
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    from invisible_playwright.constants import ARCHIVE_NAME
    asset = ARCHIVE_NAME("darwin", "arm64")

    url_archive = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/{asset}"
    url_sums = f"https://github.com/feder-cr/invisible_playwright/releases/download/{BINARY_VERSION}/checksums.txt"

    responses.add(responses.GET, url_archive, body=archive_bytes, status=200)
    responses.add(responses.GET, url_sums, body=f"{archive_sha}  {asset}\n", status=200)

    monkeypatch.setattr("sys.platform", "darwin")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    with pytest.raises(RuntimeError, match="binary not found after extraction"):
        ensure_binary()
