---
date: "2026-05-14T14:55:06.486306+00:00"
git_commit: 93ef373c54f3a51c1948a6c114b2edcebbdce732
branch: feat/macos-apple-silicon-port
topic: "macOS Headless Mode via CGVirtualDisplay"
tags: [plan, macos, headless, CGVirtualDisplay, platform-port]
status: in-progress
---

# macOS Headless Mode — Implementation Plan

## Overview

Implement `headless=True` support on macOS using CGVirtualDisplay (private CoreGraphics API). This replaces the current `RuntimeError("not yet supported on macOS")` with a working virtual display that preserves the full GPU rendering pipeline for fingerprint coherence — matching the Linux (Xvfb) and Windows (CreateDesktop) approach.

## Current State Analysis

Phases 1-5 of the macOS Apple Silicon port are complete. macOS headed mode works. `headless=True` raises at `_headless.py:218-222`:

```python
if sys.platform == "darwin":
    raise RuntimeError(
        "invisible_playwright headless=True is not yet supported on macOS. "
        "Use headless=False (the default) for headed mode."
    )
```

The launcher flow (`launcher.py:289-301`, `async_api.py:169-175`) is platform-agnostic — it calls `make_virtual_display()`, `.start()`, stores the reference, and `.stop()` on teardown. Once the dispatcher returns a macOS class instead of raising, everything works.

### Key Discoveries:
- CGVirtualDisplay is an ObjC class accessible via `objc.lookUpClass()` after loading the CoreGraphics bundle — confirmed working on macOS 26.4.1 (research Q1)
- No `DISPLAY` env var equivalent on macOS; window targeting uses Approach A: make virtual display primary via `CGConfigureDisplayOrigin` (research Q2)
- CGVirtualDisplay requires active Aqua session (logged-in GUI user) — not a replacement for Xvfb (research Q4)
- macOS 14+ required (project decision, API exists since macOS 11)
- No headless-specific pref changes needed — all macOS prefs already work from Phase 2
- `pyobjc-framework-Quartz>=10.0` needed as platform-conditional dependency

### Open Questions (to be resolved by Phase 1 spike):
1. Does Firefox render correctly on a CGVirtualDisplay (`CGDisplayIsActive()` returns 0)?
2. Does making the virtual display primary actually route Firefox windows to it?
3. Is `IOPMAssertionCreateWithName` needed to prevent the virtual display from sleeping?
4. Does cleanup work reliably (display destroyed when Python object released)?

## Desired End State

- `InvisiblePlaywright(headless=True)` works on macOS with an active Aqua session
- Firefox runs on a CGVirtualDisplay, invisible to the user (briefly disrupts display arrangement during launch)
- Clear error messages for: missing PyObjC, macOS < 14, no Aqua session
- All existing tests pass, new tests cover macOS headless paths
- CLAUDE.md and README.md updated

### Verification:
- `pytest` — all tests pass
- Manual: `InvisiblePlaywright(headless=True)` on macOS launches Firefox invisibly, pages render correctly

## What We're NOT Doing

- Accessibility API window targeting (Approach B) — deferred; Approach A (make primary) avoids permission requirements
- macOS CI/CD pipeline setup
- Headless support in SSH-only environments (requires Aqua session, documented limitation)
- Calibrating font factors or WebGL parameters (separate task)
- HiDPI / Retina support for the virtual display

## Implementation Approach

Three phases:

1. **Spike** — empirical testing on macOS to answer the 4 open questions. Results determine Phase 2 design.
2. **Implementation** — `_MacOSVirtualDisplay` class, dependency, dispatcher update, all test updates.
3. **Documentation** — CLAUDE.md and README.md updates.

Window targeting strategy: **Approach A (make primary)**. Move virtual display to origin (0,0) before Firefox launch, making it the primary display. Firefox windows open there. Keep virtual display as primary for the session; restore original display arrangement in `stop()`. Brief disruption to the user's display layout is acceptable for a dev tool.

---

## Phase 1: Spike — Empirical Testing

### Overview
Answer the 4 open questions from the research document by running tests on a real macOS machine with Aqua session and the patched Firefox binary. Results are documented and committed.

### Changes Required:

#### [x] 1. Create spike test script
**File**: `tests/spike_macos_headless.py`

Standalone script (not collected by pytest) that tests:

```python
"""Spike: macOS CGVirtualDisplay empirical tests.

Run manually on macOS with Aqua session:
    pip install pyobjc-framework-Quartz
    python tests/spike_macos_headless.py [--firefox-path /path/to/firefox]

Answers open questions from research doc.
"""

# Test 1: CGVirtualDisplay lifecycle
# - Create display with 1920x1080
# - Print displayID, CGDisplayPixelsWide/High
# - Print CGDisplayIsOnline(), CGDisplayIsActive()
# - Check if display appears in Quartz.CGGetActiveDisplayList()
# - Release display, verify displayID is gone

# Test 2: Approach A — make primary + Firefox rendering
# - Create virtual display
# - Record CGMainDisplayID()
# - Move virtual display to origin(0,0) via CGConfigureDisplayOrigin
# - Launch Firefox via Playwright, navigate to data:text/html page
# - Take screenshot, save to /tmp/spike_screenshot.png
# - Verify screenshot is not blank (check pixel variance)
# - Print results

# Test 3: Display sleep prevention
# - Create virtual display without IOPMAssertion
# - Wait 30s, check if display still reports valid resolution
# - Create with IOPMAssertion, repeat
# - Print whether assertion is needed

# Test 4: Cleanup verification
# - Create display, record displayID
# - Delete Python reference (del display)
# - Force GC (gc.collect())
# - Check if displayID still exists in display list
# - Print cleanup result

# Test 5: Multi-monitor layout restoration
# - Record all display origins via CGDisplayBounds() before any changes
# - Create virtual display, make primary
# - Restore all saved origins via CGConfigureDisplayOrigin
# - Compare final display origins to initial snapshot
# - Print per-display (x, y) drift (should be zero)
```

#### [x] 2. Delete spike script
**File**: `tests/spike_macos_headless.py`
The spike script is a throwaway investigation tool. Delete it once results are documented below — it should not be committed to the repository.

#### [x] 3. Document spike results
**File**: `docs/agents/research/2026-05-14-macos-headless-implementation.md`

Append a new section `## Spike Results (Phase 1)` documenting:
- Each test outcome (PASS/FAIL)
- Any unexpected behavior observed
- Design decisions informed by results
- Screenshot evidence (path or description)

### Success Criteria:

#### Automated Verification:
- [x] `pytest` — full suite still passes (spike script not collected)

#### Manual Verification:
- [x] All 5 spike tests executed on macOS with Aqua session (Test 2 skipped — no Firefox binary cached)
- [x] Results documented in research doc
- [ ] Firefox screenshot is not blank (proves rendering works on virtual display) — BLOCKED: patched Firefox binary not cached
- [x] All display origins restored to pre-test positions (multi-monitor safe)
- [x] Spike script deleted after results documented

**Implementation Note**: After completing this phase and documenting results, pause for confirmation. If any spike test fails, the plan must be revised before proceeding to Phase 2.

---

## Phase 2: Implementation — `_MacOSVirtualDisplay` + Dispatcher + Tests

### Overview
Add the `_MacOSVirtualDisplay` class, wire it into the dispatcher, add `pyobjc-framework-Quartz` dependency, and update all affected tests. This is the core implementation phase.

### Changes Required:

#### [ ] 1. Add pyobjc-framework-Quartz dependency
**File**: `pyproject.toml`
**After line 27** (after the pywin32 dependency):

```toml
"pyobjc-framework-Quartz>=10.0; sys_platform == 'darwin'",
```

#### [ ] 2. Implement `_MacOSVirtualDisplay`
**File**: `src/invisible_playwright/_headless.py`
**After**: `_WindowsVirtualDesktop` class (line 210), before `make_virtual_display()`

```python
class _MacOSVirtualDisplay:
    """A CGVirtualDisplay that Firefox renders onto.

    Creates a virtual monitor via CoreGraphics and makes it the primary
    display so new windows (Firefox) open there. The user's physical
    display arrangement is briefly disrupted during the browser session
    and restored on stop().

    Requires macOS 14+ and an active Aqua session (logged-in GUI user
    with WindowServer running). Does not work in SSH-only or headless CI
    environments without auto-login configured.
    """

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self._width = width
        self._height = height
        self._display = None          # CGVirtualDisplay ObjC object
        self._display_id = None       # CGDirectDisplayID (int)
        self._saved_origins = {}      # {display_id: (x, y)} to restore on stop()

    def start(self) -> None:
        try:
            import objc
            import Quartz
        except ImportError as e:
            raise RuntimeError(
                "invisible_playwright headless=True on macOS requires "
                "pyobjc-framework-Quartz. "
                "Install it: pip install pyobjc-framework-Quartz"
            ) from e

        objc.loadBundle(
            'CoreGraphics', globals(),
            '/System/Library/Frameworks/CoreGraphics.framework',
        )

        CGVirtualDisplay_ = objc.lookUpClass('CGVirtualDisplay')
        if CGVirtualDisplay_ is None:
            raise RuntimeError(
                "CGVirtualDisplay not available — macOS 14+ (Sonoma) required."
            )

        # Create descriptor
        Desc = objc.lookUpClass('CGVirtualDisplayDescriptor')
        desc = Desc.alloc().init()
        desc.setName_('InvisiblePlaywrightVD')
        desc.setMaxPixelsWide_(self._width)
        desc.setMaxPixelsHigh_(self._height)
        desc.setSizeInMillimeters_((800, 450))
        desc.setVendorID_(0x1234)
        desc.setProductID_(0x5678)
        desc.setSerialNum_(1)

        # Create virtual display
        self._display = CGVirtualDisplay_.alloc().initWithDescriptor_(desc)
        self._display_id = self._display.displayID()

        # Configure mode and resolution
        Mode = objc.lookUpClass('CGVirtualDisplayMode')
        mode = Mode.alloc().initWithWidth_height_refreshRate_(
            self._width, self._height, 60.0,
        )
        Settings = objc.lookUpClass('CGVirtualDisplaySettings')
        settings = Settings.alloc().init()
        settings.setModes_([mode])
        settings.setHiDPI_(0)

        if not self._display.applySettings_(settings):
            self._display = None
            self._display_id = None
            raise RuntimeError(
                "CGVirtualDisplay.applySettings_ failed. "
                "Ensure an Aqua session is active (logged-in GUI user "
                "with WindowServer running)."
            )

        # Make virtual display primary so Firefox windows open there
        self._make_primary(Quartz)

    def _save_display_layout(self, Quartz) -> None:
        err, display_ids, count = Quartz.CGGetOnlineDisplayList(16, None, None)
        if err == 0 and display_ids:
            for did in display_ids[:count]:
                bounds = Quartz.CGDisplayBounds(did)
                self._saved_origins[did] = (
                    int(bounds.origin.x), int(bounds.origin.y),
                )

    def _make_primary(self, Quartz) -> None:
        import ctypes
        self._save_display_layout(Quartz)
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        config = ctypes.c_void_p()
        cg.CGBeginDisplayConfiguration(ctypes.byref(config))
        cg.CGConfigureDisplayOrigin(config, self._display_id, 0, 0)
        # kCGConfigureForSession = 1 (temporary, not persisted across reboot)
        cg.CGCompleteDisplayConfiguration(config, 1)

    def _restore_layout(self) -> None:
        if not self._saved_origins:
            return
        import ctypes
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        config = ctypes.c_void_p()
        cg.CGBeginDisplayConfiguration(ctypes.byref(config))
        for did, (x, y) in self._saved_origins.items():
            if did == self._display_id:
                continue  # virtual display is about to be destroyed
            cg.CGConfigureDisplayOrigin(config, did, x, y)
        cg.CGCompleteDisplayConfiguration(config, 1)
        self._saved_origins.clear()

    def stop(self) -> None:
        self._restore_layout()
        self._display = None
        self._display_id = None
```

**Design notes:**
- `__init__()` does no imports — safe to instantiate on any platform (matches Windows/Linux pattern)
- `start()` imports PyObjC lazily — clear error if missing
- `stop()` is idempotent and safe to call without `start()` (matches contract)
- Display destroyed when `self._display` Python object is released (GC)
- `kCGConfigureForSession=1` — configuration is session-scoped, not persisted
- `_save_display_layout()` snapshots all physical display origins before disruption; `_restore_layout()` puts them back — correct for multi-monitor setups

**Spike-dependent adjustments:** If spike reveals that:
- IOPMAssertion is needed: add assertion creation in `start()`, release in `stop()`
- Make-primary doesn't work: pivot to different window targeting (out of scope, requires re-planning)
- Firefox doesn't render on CGVirtualDisplay: entire approach must change (re-plan)

#### [ ] 3. Update `make_virtual_display()` dispatcher
**File**: `src/invisible_playwright/_headless.py`
**Lines**: 212-225

```python
def make_virtual_display():
    """Return a started/stoppable virtual-display object for this platform."""
    if sys.platform == "win32":
        return _WindowsVirtualDesktop()
    if sys.platform.startswith("linux"):
        return _LinuxVirtualDisplay()
    if sys.platform == "darwin":
        return _MacOSVirtualDisplay()
    raise RuntimeError(
        f"invisible_playwright supports Windows, Linux, and macOS only "
        f"(got {sys.platform!r})"
    )
```

Changes:
- Darwin branch returns `_MacOSVirtualDisplay()` instead of raising
- Unsupported platform error updated to mention macOS

#### [ ] 4. Update `test_headless.py` — remove darwin-raises tests, add macOS class tests
**File**: `tests/test_headless.py`

**Remove** (no longer applicable):
- `test_make_virtual_display_raises_on_darwin`
- `test_make_virtual_display_darwin_error_suggests_headed_mode`

**Update:**
- `test_make_virtual_display_raises_on_unsupported_platform` — match `"Windows, Linux, and macOS only"`

**Add — dispatcher:**

```python
@pytest.mark.unit
def test_make_virtual_display_returns_macos_display_on_darwin(monkeypatch):
    """Dispatcher returns _MacOSVirtualDisplay on darwin."""
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    vd = make_virtual_display()
    assert isinstance(vd, _MacOSVirtualDisplay)
```

**Add — construction state (runs on any platform):**

```python
@pytest.mark.unit
def test_macos_virtual_display_initial_state_is_clean():
    """Construction must not import PyObjC or allocate resources —
    only start() does. Matches Windows/Linux pattern."""
    vd = _MacOSVirtualDisplay()
    assert vd._display is None
    assert vd._display_id is None
    assert vd._saved_origins == {}


@pytest.mark.unit
def test_macos_virtual_display_default_dimensions():
    """Default resolution matches the profile sampler's default screen."""
    vd = _MacOSVirtualDisplay()
    assert vd._width == 1920
    assert vd._height == 1080


@pytest.mark.unit
def test_macos_virtual_display_custom_dimensions():
    """Caller-supplied width/height stored for use in start()."""
    vd = _MacOSVirtualDisplay(width=2560, height=1440)
    assert vd._width == 2560
    assert vd._height == 1440


@pytest.mark.unit
def test_macos_virtual_display_stop_without_start_is_safe():
    """stop() before start() is a no-op — supports __exit__ on failed launch."""
    vd = _MacOSVirtualDisplay()
    vd.stop()
    vd.stop()
    assert vd._display is None
    assert vd._display_id is None
    assert vd._saved_origins == {}
```

**Add — start() error paths:**

```python
@pytest.mark.unit
def test_macos_start_raises_when_pyobjc_missing(monkeypatch):
    """Clear error when pyobjc-framework-Quartz is not installed."""
    import builtins
    real_import = builtins.__import__

    def block_pyobjc(name, *args, **kwargs):
        if name in ("objc", "Quartz"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_pyobjc)
    vd = _MacOSVirtualDisplay()
    with pytest.raises(RuntimeError, match="pyobjc-framework-Quartz"):
        vd.start()
```

```python
@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "darwin", reason="needs PyObjC importable")
def test_macos_start_raises_when_cgvirtualdisplay_unavailable(monkeypatch):
    """Clear error when CGVirtualDisplay class not found (macOS < 14)."""
    import objc
    monkeypatch.setattr(objc, "lookUpClass", lambda name: None)
    vd = _MacOSVirtualDisplay()
    with pytest.raises(RuntimeError, match="macOS 14"):
        vd.start()
```

#### [ ] 5. Update `test_e2e.py` — darwin headless tests
**File**: `tests/test_e2e.py`

**Replace** `test_darwin_resolve_headless_raises_not_yet_supported` with:

```python
@pytest.mark.e2e
def test_darwin_resolve_headless_creates_virtual_display(monkeypatch):
    """headless=True on darwin creates and starts a _MacOSVirtualDisplay
    instead of raising. Mirrors test_e10_linux_resolve_headless_invokes_xvfb_dispatcher."""
    monkeypatch.setattr(sys, "platform", "darwin")

    started = []
    stopped = []

    class FakeDisplay:
        def start(self):
            started.append(True)
        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(
        "invisible_playwright._headless.make_virtual_display",
        lambda: FakeDisplay(),
    )

    ip = InvisiblePlaywright(headless=True, binary_path="/fake")
    result = ip._resolve_headless()
    assert result is False
    assert len(started) == 1
    assert ip._virtual_display is not None
```

**Add:**

```python
@pytest.mark.e2e
def test_darwin_teardown_stops_virtual_display_and_is_idempotent(monkeypatch):
    """Teardown calls stop() on the virtual display. Second stop() is safe.
    Mirrors test_e11_linux_teardown_stops_virtual_display_and_is_idempotent."""
    monkeypatch.setattr(sys, "platform", "darwin")

    stop_count = []

    class FakeDisplay:
        def start(self):
            pass
        def stop(self):
            stop_count.append(True)

    monkeypatch.setattr(
        "invisible_playwright._headless.make_virtual_display",
        lambda: FakeDisplay(),
    )

    ip = InvisiblePlaywright(headless=True, binary_path="/fake")
    ip._resolve_headless()
    ip._teardown()
    ip._teardown()
    assert len(stop_count) == 1  # second teardown skips (vd set to None)
```

#### [ ] 6. Update import in `test_headless.py`
**File**: `tests/test_headless.py`

Add `_MacOSVirtualDisplay` to the import:

```python
from invisible_playwright._headless import (
    _LinuxVirtualDisplay,
    _MacOSVirtualDisplay,
    _WindowsVirtualDesktop,
    make_virtual_display,
)
```

### Success Criteria:

#### Automated Verification:
- [ ] `pytest tests/test_headless.py -v` — all pass (new + updated tests)
- [ ] `pytest tests/test_e2e.py -v` — all pass (updated darwin tests)
- [ ] `pytest` — full suite passes (no regressions)

#### Manual Verification:
- [ ] On macOS with Aqua session: `InvisiblePlaywright(headless=True, binary_path="...")` launches Firefox invisibly
- [ ] Page renders correctly (take screenshot, verify not blank)
- [ ] Display arrangement restored after browser closes
- [ ] Error message is clear when PyObjC missing

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation on macOS before proceeding to Phase 3.

---

## Phase 3: Documentation Updates

### Overview
Update CLAUDE.md and README.md to reflect macOS headless support.

### Changes Required:

#### [ ] 1. Update CLAUDE.md — Key design constraints section
**File**: `CLAUDE.md`
**Section**: "Key design constraints", bullet 2

Replace:
```
macOS headless is not yet supported (`headless=True` raises a clear error); headed mode works.
```
With:
```
macOS uses CGVirtualDisplay (private CoreGraphics API) to create a virtual monitor; requires macOS 14+ and an active Aqua session.
```

#### [ ] 2. Update CLAUDE.md — prefs.py description
**File**: `CLAUDE.md`
**Section**: "Fingerprint generation pipeline", item 3

No change needed — the prefs.py description already covers macOS correctly from Phase 2 of the port.

#### [ ] 3. Update CLAUDE.md — Architecture, _headless.py mention
**File**: `CLAUDE.md`

Add a brief mention of the three-platform virtual display system if not already present. The current text says "a virtual display (Xvfb on Linux, CreateDesktop on Windows)" — update to include macOS:

```
a virtual display (Xvfb on Linux, CreateDesktop on Windows, CGVirtualDisplay on macOS)
```

#### [ ] 4. Update README.md — macOS headless support
**File**: `README.md`

Update the macOS section to document:
- `headless=True` now works on macOS 14+ with an active Aqua session
- Requires `pyobjc-framework-Quartz` (auto-installed via pip)
- Does not work in SSH-only or headless CI without auto-login
- Brief display disruption during browser launch

### Success Criteria:

#### Automated Verification:
- [ ] `pytest` — full suite still passes (docs-only change, but verify)

#### Manual Verification:
- [ ] CLAUDE.md accurately describes current macOS headless behavior
- [ ] README.md macOS section is clear and actionable
- [ ] No stale "not yet supported" references remain in docs

**Implementation Note**: After completing this phase, pause for final review.

---

## Testing Strategy

Follow the test pyramid: many unit tests at the base, fewer integration/e2e tests at the top. Each phase includes its own tests.

### Test Design Techniques Applied

- **`_MacOSVirtualDisplay.__init__()`**: 2 dimension classes (default, custom) `[ECP]`
- **`_MacOSVirtualDisplay.start()` error paths**: 2 failure modes (PyObjC missing, CGVirtualDisplay unavailable) `[ECP]` + Aqua session failure `[ERR]`
- **`_MacOSVirtualDisplay.stop()`**: 3 states (never started, after start, double-stop) `[ST]`
- **`make_virtual_display()` dispatcher**: 4 platform classes (win32, linux, darwin, unsupported) `[ECP]`
- **Launcher integration**: headless=True creates + starts display, teardown stops it, double-teardown safe `[ST]`

### Unit Tests (test_headless.py — fast, isolated):

#### New:
- [ ] `test_make_virtual_display_returns_macos_display_on_darwin` — dispatcher returns correct type `[HAPPY]`
- [ ] `test_macos_virtual_display_initial_state_is_clean` — no resources on construction `[HAPPY]`
- [ ] `test_macos_virtual_display_default_dimensions` — 1920x1080 default `[HAPPY]`
- [ ] `test_macos_virtual_display_custom_dimensions` — caller-supplied dimensions `[ECP]`
- [ ] `test_macos_virtual_display_stop_without_start_is_safe` — idempotent stop `[ST]`
- [ ] `test_macos_start_raises_when_pyobjc_missing` — clear error message `[NEG]`
- [ ] `test_macos_start_raises_when_cgvirtualdisplay_unavailable` — macOS 14+ message (macOS-only) `[NEG]`

#### Updated:
- [ ] `test_make_virtual_display_raises_on_unsupported_platform` — new error message match `[NEG]`

#### Removed:
- [ ] `test_make_virtual_display_raises_on_darwin` — replaced by dispatch test
- [ ] `test_make_virtual_display_darwin_error_suggests_headed_mode` — no longer applicable

#### Regression — existing tests must still pass:
- [ ] All Windows dispatch + construction tests unchanged
- [ ] All Linux dispatch + construction + geometry + stop tests unchanged
- [ ] `test_make_virtual_display_error_mentions_offending_platform` — unchanged (matches platform string)

### Integration Tests (test_integration.py):

No changes needed. macOS headless requires no pref changes (research: "No headless-specific pref changes needed for macOS"). The 8 existing darwin integration tests (IT14-IT21) already cover the prefs pipeline.

### E2E Tests (test_e2e.py — launcher routing):

#### Replaced:
- [ ] `test_darwin_resolve_headless_raises_not_yet_supported` → `test_darwin_resolve_headless_creates_virtual_display` `[HAPPY]`

#### New:
- [ ] `test_darwin_teardown_stops_virtual_display_and_is_idempotent` — double teardown safe `[ST]`

#### Regression — existing darwin e2e tests must still pass:
- [ ] `test_darwin_build_prefs_omits_windows_sandbox_key` — unchanged
- [ ] `test_darwin_build_prefs_omits_xvfb_workarounds` — unchanged
- [ ] `test_darwin_build_prefs_has_gpu_renderer` — unchanged

### Manual Testing Steps:
*Cannot be automated — require real macOS + Aqua session + patched Firefox binary.*

1. Run spike script on macOS: `python tests/spike_macos_headless.py`
2. Launch `InvisiblePlaywright(headless=True, binary_path="...")` on macOS
3. Navigate to a page, take screenshot, verify rendering
4. Verify display arrangement restored after `__exit__`
5. Test error path: uninstall pyobjc-framework-Quartz, verify clear error

### Test Commands:
```bash
# Unit tests (affected files)
pytest tests/test_headless.py -v

# E2E tests (affected files)
pytest tests/test_e2e.py -v

# Full suite (verify no regressions)
pytest
```

## Performance Considerations

- CGVirtualDisplay creation is fast (~10ms based on Chromium benchmarks)
- `CGConfigureDisplayOrigin` briefly disrupts the user's display arrangement — unavoidable with Approach A
- No impact on Firefox rendering performance (virtual display uses native GPU compositing)

## Migration Notes

- Existing Windows/Linux users: zero impact
- macOS users: `headless=True` now works (previously raised). Requires macOS 14+, Aqua session.
- `pyobjc-framework-Quartz` auto-installed on macOS via pip
- GitHub Actions macOS runners work out of the box (auto-login pre-configured)
- Self-hosted macOS CI: must configure auto-login (see research doc Q4)

## References

- Research document: `docs/agents/research/2026-05-14-macos-headless-implementation.md`
- Existing plan (Phases 1-5): `docs/agents/plans/2026-05-14-macos-apple-silicon-port.md`
- Linux reference impl: `_headless.py:35-131` (`_LinuxVirtualDisplay`)
- Windows reference impl: `_headless.py:134-209` (`_WindowsVirtualDesktop`)
- Dispatcher: `_headless.py:212-225` (`make_virtual_display()`)
- Launcher flow: `launcher.py:289-301` (`_resolve_headless()`)
- Async flow: `async_api.py:169-175` (`_resolve_headless()`)
- KhaosT/CGVirtualDisplay (Swift reference): https://github.com/KhaosT/CGVirtualDisplay
- Chromium virtual_display_mac_util.mm (C++ reference)
