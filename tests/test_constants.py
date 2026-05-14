import pytest

from invisible_playwright.constants import ARCHIVE_NAME, BINARY_BASENAME, BINARY_VERSION


@pytest.mark.unit
def test_binary_version_format():
    assert BINARY_VERSION.startswith("firefox-")
    assert BINARY_VERSION.split("-", 1)[1].isdigit()


@pytest.mark.unit
def test_archive_name_windows():
    name = ARCHIVE_NAME("win32", "AMD64")
    assert name.endswith(".zip")
    assert "win-x86_64" in name


@pytest.mark.unit
def test_archive_name_linux():
    name = ARCHIVE_NAME("linux", "x86_64")
    assert name.endswith(".tar.gz")
    assert "linux-x86_64" in name


@pytest.mark.unit
def test_archive_name_macos():
    name = ARCHIVE_NAME("darwin", "arm64")
    assert name.endswith(".tar.gz")
    assert "macos-arm64" in name


@pytest.mark.unit
def test_archive_name_unsupported_platform_raises():
    with pytest.raises(NotImplementedError):
        ARCHIVE_NAME("freebsd", "x86_64")


@pytest.mark.unit
def test_archive_name_unsupported_arch_raises():
    with pytest.raises(NotImplementedError):
        ARCHIVE_NAME("linux", "mips64")


@pytest.mark.unit
def test_binary_basename_format():
    assert "firefox" in BINARY_BASENAME.lower()
    assert "stealth" in BINARY_BASENAME.lower()
