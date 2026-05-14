---
date: "2026-05-14T14:15:34.680723+00:00"
git_commit: 70c1ca464f60530d935c630381713bd4ae82e966
branch: main
topic: "macOS Apple Silicon Port (Python-side)"
tags: [plan, macos, apple-silicon, platform-port]
status: draft
---

# macOS Apple Silicon Port — Implementation Plan

## Overview

Add macOS Apple Silicon (darwin/arm64) support to the Python wrapper. The patched Firefox binary for macOS arm64 is a **hard prerequisite built separately** — this plan covers only the Python-side changes needed to launch and configure it.

Headless mode is deferred: Phase 1-5 deliver a fully functional headed-mode port. CGVirtualDisplay or NSRunningApplication.hide() can be added later.

## Current State Analysis

The codebase supports Windows x86_64 and Linux x86_64. macOS is blocked at three gating locations:

1. `constants.py:30,36` — `ARCHIVE_NAME()` rejects `arm64` arch and `darwin` platform
2. `constants.py:40-43` — `BINARY_ENTRY_REL` has no `darwin` entry
3. `_headless.py:221-222` — `make_virtual_display()` raises RuntimeError on darwin

The fingerprint pipeline (`_fpforge/`) is fully platform-agnostic. Navigator identity, system colors, speech voices, audio, proxy, WebRTC, timezone, and canvas noise all work on macOS without changes.

Platform-conditional logic in `prefs.py` needs darwin branches in 4 locations:
- GPU/WebGL renderer spoofing (line 452-458)
- MSAA sample count (line 465)
- WebGL extensions whitelist (line 542-544)
- Font metrics compensation (line 400-418)

### Key Discoveries:
- macOS Firefox uses CGL/OpenGL (not ANGLE) — reports `"Apple M1"` for all Apple Silicon
- Must spoof renderer to Windows ANGLE format (same approach as Linux)
- CoreText font widths differ from DirectWrite — needs `_MACOS_GENERIC_FONT_FACTORS`
- macOS CGL extensions differ from ANGLE — must apply extension whitelist (like Linux)
- Linux Xvfb workarounds must NOT be applied on macOS (WebRender works natively)
- Windows virtual desktop workarounds must NOT be applied on macOS
- `.app` bundle binary path: `Firefox.app/Contents/MacOS/firefox`
- Archive format: `.tar.gz` (same as Linux)

## Desired End State

- `InvisiblePlaywright(binary_path="/path/to/firefox")` works on macOS arm64 in headed mode
- `ensure_binary()` downloads and extracts the macOS arm64 archive
- `translate_profile_to_prefs()` produces correct macOS-specific prefs (GPU spoofing, extension whitelist, font compensation)
- `headless=True` raises a clear "not yet supported on macOS" error (not a crash)
- All existing Windows/Linux tests still pass
- New darwin-specific tests cover each changed code path

### Verification:
- `pytest` — all tests pass (existing + new darwin tests)
- Manual: launch Firefox on macOS with `binary_path=` pointing to local build, verify fingerprint coherence

## What We're NOT Doing

- Building the patched Firefox binary (separate C++/Mozilla build system task)
- Implementing macOS headless mode (CGVirtualDisplay / NSRunningApplication.hide())
- Calibrating `_MACOS_GENERIC_FONT_FACTORS` (requires running Firefox + FP Pro probes — empirical)
- Calibrating WebGL parameter overrides for Apple Silicon
- Adding macOS to CI/CD pipeline
- PyObjC dependency (not needed until headless implementation)

## Implementation Approach

Five phases, each independently testable and committable:

1. **constants.py** — ungate darwin/arm64 archive name + binary path
2. **prefs.py** — add darwin branches in 4 platform conditionals + placeholder font factors
3. **_headless.py** — explicit "not yet supported" error for darwin headless
4. **Integration tests** — darwin pipeline tests (profile → prefs → proxy)
5. **E2E tests** — darwin launcher routing tests

No changes needed in `launcher.py`, `async_api.py`, or `download.py` — they already work once `constants.py` is updated.

---

## Phase 1: Constants — Ungate darwin/arm64

### Overview
Add macOS arm64 support to `ARCHIVE_NAME()` and `BINARY_ENTRY_REL`. This unblocks `ensure_binary()` for macOS.

### Changes Required:

#### [x] 1. Accept arm64 architecture
**File**: `src/invisible_playwright/constants.py`
**Lines**: 27-30

```python
if m in {"amd64", "x86_64"}:
    arch = "x86_64"
elif m == "arm64":
    arch = "arm64"
else:
    raise NotImplementedError(f"unsupported arch: {machine}")
```

#### [x] 2. Accept darwin platform
**File**: `src/invisible_playwright/constants.py`
**Lines**: 32-36

```python
if pk == "win32":
    return f"{BINARY_BASENAME}-win-{arch}.zip"
if pk == "linux":
    return f"{BINARY_BASENAME}-linux-{arch}.tar.gz"
if pk == "darwin":
    return f"{BINARY_BASENAME}-macos-{arch}.tar.gz"
raise NotImplementedError(f"unsupported platform: {platform_key}")
```

#### [x] 3. Add darwin binary entry path
**File**: `src/invisible_playwright/constants.py`
**Lines**: 40-43

```python
BINARY_ENTRY_REL = {
    "win32": "firefox.exe",
    "linux": "firefox",
    "darwin": "Firefox.app/Contents/MacOS/firefox",
}
```

#### [x] 4. Update existing "unsupported" test to use a truly unsupported platform
**File**: `tests/test_constants.py`

The existing `test_archive_name_unsupported_raises` calls `ARCHIVE_NAME("darwin", "arm64")` expecting NotImplementedError. This must change since darwin is now supported.

```python
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
```

#### [x] 5. Add darwin download tests
**File**: `tests/test_download.py`

Add macOS-specific download/extraction tests mirroring the Linux `.tar.gz` test pattern. Update `test_ensure_binary_unsupported_platform_raises` to use `"freebsd"` instead of `"darwin"`.

New tests:
- `test_ensure_binary_downloads_and_verifies_macos` — mock darwin/arm64, verify `.tar.gz` download + extraction + `.app` bundle path
- `test_ensure_binary_rejects_sha_mismatch_macos` — SHA256 mismatch on macOS archive
- `test_ensure_binary_cache_hit_skips_http_macos` — cached macOS binary skips download
- `test_ensure_binary_missing_entry_after_extract_raises_macos` — missing `.app` bundle entry

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_constants.py -v` — all pass
- [x] `pytest tests/test_download.py -v` — all pass
- [x] `pytest` — full suite passes (no regressions)

#### Manual Verification:
- [x] Review that `ARCHIVE_NAME("darwin", "arm64")` returns `"firefox-150.0.1-stealth-macos-arm64.tar.gz"`
- [x] Review that `BINARY_ENTRY_REL["darwin"]` returns `"Firefox.app/Contents/MacOS/firefox"`

#### Phase Gate:
- [x] Run `pytest` — all tests pass, fix any failures
- [x] Update plan status for Phase 1 to `complete`
- [x] Commit changes
- [x] Pause for confirmation before proceeding to Phase 2

---

## Phase 2: Prefs — Add darwin platform branches

### Overview
Add macOS handling to the 4 platform-conditional blocks in `prefs.py`. macOS gets the same treatment as Linux: spoof GPU renderer to ANGLE format, apply extension whitelist, set MSAA from profile, add font compensation factors.

### Changes Required:

#### [x] 1. Add `_MACOS_GENERIC_FONT_FACTORS` placeholder
**File**: `src/invisible_playwright/prefs.py`
**After**: `_LINUX_GENERIC_FONT_FACTORS` (line 191)

```python
# macOS font compensation — CoreText advances differ from DirectWrite.
# PLACEHOLDER: values copied from Linux as a starting point. Must be
# calibrated empirically by measuring CoreText rendering of FP Pro probe
# strings and comparing to Windows DirectWrite target widths.
_MACOS_GENERIC_FONT_FACTORS = (
    "serif|0.920,sans-serif|0.889,monospace|1.000,"
    "system-ui|0.910,cursive|0.932,fantasy|0.812,"
)
```

#### [x] 2. Update `_font_metrics_for_platform()` for darwin
**File**: `src/invisible_playwright/prefs.py`
**Lines**: 400-418

```python
def _font_metrics_for_platform(profile_metrics: str) -> str:
    if not profile_metrics:
        return ""
    if sys.platform.startswith("linux"):
        return _LINUX_GENERIC_FONT_FACTORS + profile_metrics
    if sys.platform == "darwin":
        return _MACOS_GENERIC_FONT_FACTORS + profile_metrics
    return ""  # Windows: NEVER apply width-scale factors.
```

#### [x] 3. Update GPU renderer block for darwin
**File**: `src/invisible_playwright/prefs.py`
**Lines**: 452-459

macOS must spoof renderer to ANGLE format (same as Linux). Change the `if/else` to `if/elif/else`:

```python
if sys.platform.startswith("linux") or sys.platform == "darwin":
    prefs["zoom.stealth.webgl.renderer"] = profile.gpu.renderer
    prefs["zoom.stealth.webgl.vendor"]   = profile.gpu.vendor
    _renderer_lo = (profile.gpu.renderer or "").lower()
else:
    prefs["zoom.stealth.webgl.renderer"] = ""
    prefs["zoom.stealth.webgl.vendor"]   = ""
    _renderer_lo = "intel"  # test hardware is Intel Arc A750
```

#### [x] 4. Update MSAA block for darwin
**File**: `src/invisible_playwright/prefs.py`
**Line**: 465

```python
_msaa = profile.webgl.msaa_samples if (
    sys.platform.startswith("linux") or sys.platform == "darwin"
) else 4
```

#### [x] 5. Update WebGL extensions block for darwin
**File**: `src/invisible_playwright/prefs.py`
**Lines**: 542-544

macOS needs the curated extension list (like Linux), not the native CGL list. Change the condition:

```python
if sys.platform == "win32":
    prefs["zoom.stealth.webgl.extensions"]  = ""
    prefs["zoom.stealth.webgl2.extensions"] = ""
```

This replaces `if not sys.platform.startswith("linux")` — now only Windows clears extensions. Both Linux and macOS keep the curated baseline list.

#### [x] 6. Verify Xvfb workarounds are NOT applied on darwin
**File**: `src/invisible_playwright/prefs.py`
**Lines**: 547-549

No change needed. The existing `if sys.platform.startswith("linux")` correctly excludes darwin. Verify with a test.

#### [x] 7. Verify Windows virtual desktop workarounds are NOT applied on darwin
**File**: `src/invisible_playwright/prefs.py`
**Lines**: 552-554

No change needed. The existing `if virtual_display and sys.platform == "win32"` correctly excludes darwin. Verify with a test.

#### [x] 8. Add darwin prefs tests
**File**: `tests/test_prefs.py`

New tests (mirroring existing Linux/Windows test patterns):

- `test_font_metrics_darwin_prepends_generic_factors` — verify `_MACOS_GENERIC_FONT_FACTORS` prepended `[HAPPY]`
- `test_font_metrics_darwin_empty_input_returns_empty` — empty profile_metrics → empty string `[ECP]`
- `test_gpu_renderer_set_from_profile_on_darwin` — renderer/vendor from profile `[HAPPY]`
- `test_msaa_from_profile_on_darwin` — MSAA from profile, not pinned to 4 `[HAPPY]`
- `test_msaa_zero_disables_force_on_darwin` — MSAA 0 → webgl.msaa-force = False `[BVA]`
- `test_canvas_noise_mask_intel_on_darwin` — "intel" in renderer → skip_mask 15 `[HAPPY]`
- `test_canvas_noise_mask_nvidia_on_darwin` — "nvidia" in renderer → skip_mask 7 `[ECP]`
- `test_webgl_extensions_preserved_on_darwin` — extensions NOT cleared `[HAPPY]`
- `test_xvfb_workarounds_absent_on_darwin` — no Xvfb prefs `[NEG]`
- `test_virtual_display_no_op_on_darwin` — no Windows sandbox prefs `[NEG]`

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_prefs.py -v` — all pass (existing + new darwin tests)
- [x] `pytest` — full suite passes

#### Manual Verification:
- [ ] Review that darwin prefs match Linux pattern (GPU spoofing, extension whitelist, font factors)
- [ ] Review that no Windows-only or Linux-only prefs leak into darwin

#### Phase Gate:
- [x] Run `pytest` — all tests pass, fix any failures
- [x] Update plan status for Phase 2 to `complete`
- [x] Commit changes
- [ ] Pause for confirmation before proceeding to Phase 3

---

## Phase 3: Headless — Explicit "not yet supported" error for darwin

### Overview
Replace the generic "Windows and Linux only" RuntimeError with a clear "macOS headless not yet supported" message. This distinguishes "we know about macOS but headless isn't implemented yet" from "we don't support this platform at all."

### Changes Required:

#### [x] 1. Add darwin-specific error in `make_virtual_display()`
**File**: `src/invisible_playwright/_headless.py`
**Lines**: 212-223

```python
def make_virtual_display():
    """Return a started/stoppable virtual-display object for this platform."""
    if sys.platform == "win32":
        return _WindowsVirtualDesktop()
    if sys.platform.startswith("linux"):
        return _LinuxVirtualDisplay()
    if sys.platform == "darwin":
        raise RuntimeError(
            "invisible_playwright headless=True is not yet supported on macOS. "
            "Use headless=False (the default) for headed mode."
        )
    raise RuntimeError(
        f"invisible_playwright supports Windows and Linux only (got {sys.platform!r})"
    )
```

#### [x] 2. Update darwin headless test
**File**: `tests/test_headless.py`

Update `test_make_virtual_display_raises_on_darwin` — match the new error message:

```python
@pytest.mark.unit
def test_make_virtual_display_raises_on_darwin(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="not yet supported on macOS"):
        make_virtual_display()
```

Add: `test_make_virtual_display_darwin_error_suggests_headed_mode` — verify the error message mentions `headless=False`:

```python
@pytest.mark.unit
def test_make_virtual_display_darwin_error_suggests_headed_mode(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="headless=False"):
        make_virtual_display()
```

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_headless.py -v` — all pass
- [x] `pytest` — full suite passes

#### Manual Verification:
- [ ] Review error message is clear and actionable

#### Phase Gate:
- [x] Run `pytest` — all tests pass, fix any failures
- [x] Update plan status for Phase 3 to `complete`
- [x] Commit changes
- [ ] Pause for confirmation before proceeding to Phase 4

---

## Phase 4: Integration Tests — Darwin pipeline

### Overview
Add integration tests that exercise the full darwin pipeline: `generate_profile()` → `translate_profile_to_prefs()` → `configure_proxy()`. These verify that all Phase 2 pref changes work correctly together.

### Changes Required:

#### [x] 1. Add darwin integration tests
**File**: `tests/test_integration.py`

New tests (mirroring existing Linux/Windows integration test patterns):

- `test_darwin_pipeline_produces_valid_prefs` — full pipeline with monkeypatched darwin, verify required keys present `[HAPPY]`
- `test_darwin_gpu_renderer_from_profile_in_pipeline` — renderer flows through from profile to prefs `[HAPPY]`
- `test_darwin_font_metrics_include_generic_factors` — font metrics prepended with macOS factors `[HAPPY]`
- `test_darwin_webgl_extensions_not_cleared_in_pipeline` — extensions preserved through pipeline `[HAPPY]`
- `test_darwin_xvfb_workarounds_absent_in_pipeline` — no Xvfb prefs in output `[NEG]`
- `test_darwin_virtual_display_workarounds_absent_in_pipeline` — no Windows sandbox prefs `[NEG]`
- `test_darwin_msaa_pin_propagates_through_pipeline` — MSAA from profile, not hardcoded 4 `[HAPPY]`
- `test_darwin_socks_proxy_with_prefs` — SOCKS5 proxy + darwin prefs compose correctly `[HAPPY]`

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_integration.py -v` — all pass
- [x] `pytest` — full suite passes

#### Phase Gate:
- [x] Run `pytest` — all tests pass, fix any failures
- [x] Update plan status for Phase 4 to `complete`
- [x] Commit changes
- [ ] Pause for confirmation before proceeding to Phase 5

---

## Phase 5: E2E Tests — Darwin launcher routing

### Overview
Add e2e tests that verify the launcher correctly routes darwin through the prefs pipeline and handles headless=True with the correct error. Update the `firefox_binary` fixture skip logic.

### Changes Required:

#### [ ] 1. Update `firefox_binary` fixture skip logic
**File**: `tests/test_e2e.py`

The fixture checks `sys.platform in BINARY_ENTRY_REL` (line 28). Since `BINARY_ENTRY_REL` now includes `"darwin"`, the fixture will no longer auto-skip on macOS — it will try to find a cached binary. If no binary is present, the existing skip at line 33-36 handles it. No change needed.

#### [ ] 2. Add darwin launcher tests
**File**: `tests/test_e2e.py`

New tests (do NOT require the patched binary — use monkeypatch):

- `test_darwin_build_prefs_omits_windows_sandbox_key` — verify `security.sandbox.gpu.level` absent from darwin prefs `[HAPPY]`
- `test_darwin_build_prefs_omits_xvfb_workarounds` — verify `gfx.webrender.force-disabled` absent `[NEG]`
- `test_darwin_resolve_headless_raises_not_yet_supported` — headless=True on darwin raises clear error `[HAPPY]`
- `test_darwin_build_prefs_has_gpu_renderer` — GPU renderer from profile present in prefs `[HAPPY]`

### Success Criteria:

#### Automated Verification:
- [ ] `pytest tests/test_e2e.py -v` — all pass
- [ ] `pytest` — full suite passes (all 100+ tests green)

#### Phase Gate:
- [ ] Run `pytest` — all tests pass, fix any failures
- [ ] Update plan status for Phase 5 to `complete`
- [ ] Commit changes
- [ ] Pause for confirmation before proceeding

---

## Testing Strategy

Follow the test pyramid: many unit tests at the base, fewer integration tests in the middle, fewest e2e tests at the top.

### Test Design Techniques Applied

Each changed function is tested by systematically partitioning inputs:

- **`ARCHIVE_NAME()`**: 3 valid platform classes (win32, linux, darwin) x 2 arch classes (x86_64/AMD64, arm64) + 2 invalid classes (unsupported platform, unsupported arch) `[ECP]`
- **`_font_metrics_for_platform()`**: 3 platform classes (linux, darwin, win32) x 2 input classes (empty, non-empty) `[ECP]`
- **GPU renderer block**: 3 platform classes → 2 behaviors (spoof vs native) `[ECP]`
- **MSAA block**: 3 platform classes → 2 behaviors (profile-derived vs pinned-4) `[ECP]`
- **WebGL extensions**: 3 platform classes → 2 behaviors (curated list vs cleared) `[ECP]`

### Unit Tests (base — fast, isolated):

#### New darwin tests in test_constants.py:
- [ ] `test_archive_name_macos` — `ARCHIVE_NAME("darwin", "arm64")` returns `"...-macos-arm64.tar.gz"` `[HAPPY]`
- [ ] `test_archive_name_unsupported_platform_raises` — `"freebsd"` raises `[NEG]`
- [ ] `test_archive_name_unsupported_arch_raises` — `"mips64"` raises `[NEG]`

#### New darwin tests in test_download.py:
- [ ] `test_ensure_binary_downloads_and_verifies_macos` — full download + extract flow `[HAPPY]`
- [ ] `test_ensure_binary_rejects_sha_mismatch_macos` — SHA mismatch detection `[NEG]`
- [ ] `test_ensure_binary_cache_hit_skips_http_macos` — cache hit skips network `[HAPPY]`
- [ ] `test_ensure_binary_missing_entry_after_extract_raises_macos` — missing binary in .app bundle `[NEG]`

#### New darwin tests in test_prefs.py:
- [ ] `test_font_metrics_darwin_prepends_generic_factors` `[HAPPY]`
- [ ] `test_font_metrics_darwin_empty_input_returns_empty` `[ECP]`
- [ ] `test_gpu_renderer_set_from_profile_on_darwin` `[HAPPY]`
- [ ] `test_msaa_from_profile_on_darwin` `[HAPPY]`
- [ ] `test_msaa_zero_disables_force_on_darwin` `[BVA]`
- [ ] `test_canvas_noise_mask_intel_on_darwin` `[HAPPY]`
- [ ] `test_canvas_noise_mask_nvidia_on_darwin` `[ECP]`
- [ ] `test_webgl_extensions_preserved_on_darwin` `[HAPPY]`
- [ ] `test_xvfb_workarounds_absent_on_darwin` `[NEG]`
- [ ] `test_virtual_display_no_op_on_darwin` `[NEG]`

#### Updated darwin tests in test_headless.py:
- [x] `test_make_virtual_display_raises_on_darwin` — updated match string `[HAPPY]`
- [x] `test_make_virtual_display_darwin_error_suggests_headed_mode` `[HAPPY]`

#### Regression — Existing tests must still pass:
- [ ] All existing `test_constants.py` tests (except updated unsupported test)
- [ ] All existing `test_download.py` tests
- [ ] All existing `test_prefs.py` tests (Windows + Linux branches unchanged)
- [ ] All existing `test_headless.py` tests (except updated darwin test)

### Integration Tests (middle):
- [ ] `test_darwin_pipeline_produces_valid_prefs` `[HAPPY]`
- [ ] `test_darwin_gpu_renderer_from_profile_in_pipeline` `[HAPPY]`
- [ ] `test_darwin_font_metrics_include_generic_factors` `[HAPPY]`
- [ ] `test_darwin_webgl_extensions_not_cleared_in_pipeline` `[HAPPY]`
- [ ] `test_darwin_xvfb_workarounds_absent_in_pipeline` `[NEG]`
- [ ] `test_darwin_virtual_display_workarounds_absent_in_pipeline` `[NEG]`
- [ ] `test_darwin_msaa_pin_propagates_through_pipeline` `[HAPPY]`
- [ ] `test_darwin_socks_proxy_with_prefs` `[HAPPY]`

### E2E Tests (top — launcher routing):
- [ ] `test_darwin_build_prefs_omits_windows_sandbox_key` `[HAPPY]`
- [ ] `test_darwin_build_prefs_omits_xvfb_workarounds` `[NEG]`
- [ ] `test_darwin_resolve_headless_raises_not_yet_supported` `[HAPPY]`
- [ ] `test_darwin_build_prefs_has_gpu_renderer` `[HAPPY]`

### Test Commands:
```bash
# Unit tests (all affected files)
pytest tests/test_constants.py tests/test_download.py tests/test_prefs.py tests/test_headless.py -v

# Integration tests
pytest tests/test_integration.py -v

# E2E tests
pytest tests/test_e2e.py -v

# Full suite (verify no regressions)
pytest
```

## Performance Considerations

None. All changes are conditional branches in existing code paths. No new dependencies, no new processes, no new I/O.

## Migration Notes

- Existing users on Windows/Linux: zero impact (no behavior change)
- macOS users: need the patched Firefox binary (separate build) + `binary_path=` kwarg until binary hosting is set up
- `headless=True` on macOS will raise a clear error directing users to headed mode

## References

- Research document: `docs/agents/research/2026-05-14-macos-apple-silicon-port.md`
- Existing Linux platform port pattern: `prefs.py:452-549`, `_headless.py:35-131`
- Firefox macOS binary structure: `Firefox.app/Contents/MacOS/firefox`
- CoreText font metrics: `gfxMacFont::GetGlyphWidth()` using `CTFontGetAdvancesForGlyphs()`
