---
date: "2026-05-14T14:39:19.240955+00:00"
git_commit: 93ef373c54f3a51c1948a6c114b2edcebbdce732
branch: feat/macos-apple-silicon-port
topic: "What's needed to implement macOS headless mode"
tags: [research, codebase, macos, headless, CGVirtualDisplay, platform-port]
status: complete
---

# Research: What's Needed to Implement macOS Headless Mode

## Research Question

What's needed to implement headless mode on macOS? What exists today, what's missing, and what are the viable approaches?

## Summary

macOS headless mode is the one remaining gap after the macOS Apple Silicon port (Phases 1-5, complete on this branch). Today, `headless=True` on macOS raises `RuntimeError("invisible_playwright headless=True is not yet supported on macOS")`. Three approaches exist: CGVirtualDisplay (best parity with Linux/Windows), NSRunningApplication.hide() (simplest), and offscreen positioning (non-viable). The implementation touches `_headless.py` (new class), `pyproject.toml` (new dependency), and test files.

## Current State

### How headless works on Linux and Windows

The project never uses Firefox's native `headless=True` mode. Instead, it runs Firefox in **headed mode on an invisible display surface**, preserving the full GPU rendering pipeline needed for fingerprint coherence.

| Platform | Mechanism | Class | File |
|----------|-----------|-------|------|
| Linux | Xvfb (X virtual framebuffer) | `_LinuxVirtualDisplay` | `_headless.py:35-131` |
| Windows | `CreateDesktop()` (hidden Win32 desktop) | `_WindowsVirtualDesktop` | `_headless.py:134-209` |
| macOS | Not implemented | N/A | `_headless.py:218-222` (raises) |

The flow (`launcher.py:289-301`):
1. `_resolve_headless()` calls `make_virtual_display()`
2. Platform dispatcher returns the appropriate class
3. `.start()` creates the invisible display
4. Firefox launches in headed mode on that display
5. `.stop()` in `_teardown()` cleans up

### Where macOS is blocked

`_headless.py:218-222` — the `make_virtual_display()` dispatcher has an explicit darwin guard:

```python
if sys.platform == "darwin":
    raise RuntimeError(
        "invisible_playwright headless=True is not yet supported on macOS. "
        "Use headless=False (the default) for headed mode."
    )
```

### Why Firefox's native headless mode is not viable

Firefox's `HeadlessWidget` code path disables WebGL entirely (Bug 1375585) — `canvas.getContext('webgl')` returns `null`. No WebGL means no renderer strings, no extensions, no parameter hashes. Fingerprint coherence breaks completely.

This is why the project uses virtual displays on all platforms instead of `headless=True`.

## Viable Approaches

### Approach 1: CGVirtualDisplay (recommended)

**What it is:** A private CoreGraphics API (exists since macOS 11, project requires macOS 14+) that creates a real system display with full GPU compositing. Unlike Xvfb, it does not create a display server — it adds a virtual monitor to the existing WindowServer (requires Aqua session).

**API surface** (from reverse-engineered macOS headers):
- `CGVirtualDisplayDescriptor` — vendorID, productID, serialNum, name, sizeInMillimeters, maxPixelsWide/High
- `CGVirtualDisplayMode` — width, height, refreshRate
- `CGVirtualDisplaySettings` — array of modes
- The display gets a real `displayID` visible to the system

**Access from Python:** `objc.lookUpClass('CGVirtualDisplay')` — not wrapped by pyobjc-framework-Quartz, needs manual ObjC bridge or ctypes.

**Production users:** Chromium test infrastructure (`ui/display/mac/test/virtual_display_mac_util.mm`), DisplayLink vendors, multiple open-source tools demonstrate stable usage across macOS 13-15.

**Pros:**
- True invisible display with full GPU compositing
- Closest architectural match to the Linux Xvfb approach
- Firefox gets the full `nsCocoaWindow` widget path with CoreAnimation + OpenGL

**Cons:**
- Private API, could break between macOS versions (mitigated by Chromium reliance)
- Project requires macOS 14+ (API exists since macOS 11)
- Requires `pyobjc-framework-Quartz>=10.0` dependency (platform-conditional)
- Requires active Aqua session (logged-in GUI user with WindowServer running) — NOT a replacement for Xvfb
- Window targeting requires either making virtual display primary (briefly disruptive) or Accessibility API permissions

**Dependency change** (`pyproject.toml`):
```toml
"pyobjc-framework-Quartz>=10.0; sys_platform == 'darwin'",
```

### Approach 2: NSRunningApplication.hide()

**What it is:** Cross-process window hiding via Cocoa API. Hides all windows of the Firefox process after launch.

**Access from Python:** `NSRunningApplication.runningApplicationWithProcessIdentifier_(pid).hide()` via PyObjC.

**Pros:**
- Simple implementation (~20 lines)
- Works on all macOS versions (no version restriction)
- No private APIs

**Cons:**
- Window flashes briefly at launch before `hide()` takes effect
- User can accidentally un-hide (Cmd+Tab, Mission Control)
- Less robust than a true virtual display — the display surface is still the user's screen

**Dependency change** (`pyproject.toml`):
```toml
"pyobjc-framework-Cocoa>=10.0; sys_platform == 'darwin'",
```

### Approach 3: Offscreen window positioning (non-viable)

macOS `NSWindow.constrainFrameRect` snaps windows back onto visible screen area. Not overridable cross-process. This approach does not work.

## Implementation Scope for CGVirtualDisplay (Approach 1)

### Files to change

| File | Change | Effort |
|------|--------|--------|
| `src/invisible_playwright/_headless.py` | New `_MacOSVirtualDisplay` class + update dispatcher | ~80-100 lines |
| `pyproject.toml` | Add `pyobjc-framework-Quartz` conditional dep | 1 line |
| `tests/test_headless.py` | New macOS virtual display tests | ~40-60 lines |
| `tests/test_e2e.py` | Update darwin headless tests (remove "not supported" expectation) | ~20 lines |
| `tests/test_integration.py` | Update darwin pipeline tests if headless prefs change | ~10 lines |

### What `_MacOSVirtualDisplay` needs to do

Following the pattern of `_LinuxVirtualDisplay` and `_WindowsVirtualDesktop`:

1. **`__init__()`** — store width/height, initialize state
2. **`start()`** — create CGVirtualDisplay with specified resolution, verify display ID is registered
3. **`stop()`** — tear down the virtual display, clean up
4. **Error handling** — clear error if PyObjC not installed, clear error if CGVirtualDisplay not available (macOS < 14)

The class must be idempotent on `stop()` (safe to call without `start()`, safe to call twice) — matching the existing contract tested in `test_headless.py`.

### Open technical questions for implementation

1. **ObjC bridge vs ctypes:** `CGVirtualDisplay` is not wrapped by pyobjc-framework-Quartz. Options:
   - Manual `objc.lookUpClass('CGVirtualDisplay')` + attribute access
   - ctypes against CoreGraphics.framework
   - Which is more maintainable?

2. **Display targeting:** After creating the virtual display, how does Firefox target it? On Linux, `DISPLAY=:99` routes X11 clients. On Windows, thread desktop inheritance. On macOS:
   - Firefox may need specific environment or launch configuration to use the virtual display
   - CoreGraphics may route windows to the most recently added display, or may not
   - Needs empirical testing

3. **macOS version gating:** CGVirtualDisplay requires macOS 14+. What error message for macOS 13 and below? Fall back to NSRunningApplication.hide(), or refuse?

4. **CI/SSH compatibility:** Running headed Firefox on macOS requires an Aqua session (logged-in GUI user). The virtual display may or may not solve this for SSH/CI environments. Needs testing.

## What Already Exists (no changes needed)

These components work correctly on macOS today:

- **Headless flow in launcher** (`launcher.py:289-301`, `async_api.py:169-175`): Platform-agnostic — calls `make_virtual_display()` and `.start()`, stores reference, returns `False`. Just needs the dispatcher to return a macOS class instead of raising.
- **Teardown** (`launcher.py:241-246`): Calls `.stop()` on whatever virtual display object exists. Platform-agnostic.
- **`virtual_display` pref flag** (`launcher.py:258`): Only `True` on Windows (`self._headless and _sys.platform == "win32"`). macOS does not need the `security.sandbox.gpu.level=0` workaround. No change needed.
- **Prefs pipeline**: All macOS-specific prefs (GPU spoofing, font factors, extensions, MSAA) already implemented in Phase 2 of the port. No headless-specific pref changes needed for macOS.

## Existing Test Coverage for Headless

### test_headless.py (10 tests)
- Platform dispatch: Windows, Linux, Linux variants, darwin (currently expects error)
- Windows desktop: initial state, stop idempotency
- Linux Xvfb: initial state, geometry default/custom, stop safety

### test_e2e.py (headless-related, 7 tests)
- Linux: Xvfb invocation, teardown idempotency, missing Xvfb error, no Windows sandbox key
- Darwin: headless raises "not yet supported", no Windows sandbox key, no Xvfb workarounds

### test_prefs.py (headless-related, 7 tests)
- Windows virtual_display workaround on/off
- Linux/darwin virtual_display no-op
- Xvfb workarounds: applied on Linux, absent on Windows, absent on darwin

### Tests to add/update for macOS headless
- `test_headless.py`: `test_make_virtual_display_returns_macos_display_on_darwin` (dispatch), `test_macos_virtual_display_initial_state_is_clean`, `test_macos_virtual_display_stop_is_idempotent_without_start`
- `test_headless.py`: Remove or update `test_make_virtual_display_raises_on_darwin`
- `test_e2e.py`: Update `test_darwin_resolve_headless_raises_not_yet_supported` to test successful headless resolution instead

## Architecture Documentation

### Virtual display class contract

All virtual display classes follow the same interface (informal, no ABC):

```python
class _PlatformVirtualDisplay:
    def __init__(self, width=1920, height=1080): ...
    def start(self) -> None: ...   # Create and activate display
    def stop(self) -> None: ...    # Tear down; idempotent; safe without start()
```

The dispatcher `make_virtual_display()` returns an **unstarted** instance. The caller (`_resolve_headless()`) calls `.start()` and stores the reference. Teardown calls `.stop()`.

### Platform-specific considerations

| Concern | Linux (Xvfb) | Windows (CreateDesktop) | macOS (CGVirtualDisplay) |
|---------|-------------|------------------------|-------------------------|
| Targets display via | `DISPLAY` env var | Thread desktop inheritance | Coordinate-based (make primary or AX move) |
| Requires GUI session | No (Xvfb IS the server) | No (desktop is kernel object) | **Yes** (needs running WindowServer) |
| Cleanup on crash | Xvfb process orphaned | Desktop handle leaked | Display destroyed when Python object GC'd |
| Concurrent instances | Yes (different :N) | Yes (different desktop names) | Yes (different display IDs) |
| GPU compositing | Via GLX extension | Via WARP/D3D11 | Via CGL/OpenGL natively |
| Special sandbox prefs | No | `security.sandbox.gpu.level=0` | No |
| Xvfb/WebRender workarounds | Yes (force-disable WebRender) | No | No |

## Code References

- `src/invisible_playwright/_headless.py:35-131` — `_LinuxVirtualDisplay` (reference implementation)
- `src/invisible_playwright/_headless.py:134-209` — `_WindowsVirtualDesktop` (reference implementation)
- `src/invisible_playwright/_headless.py:212-225` — `make_virtual_display()` dispatcher
- `src/invisible_playwright/_headless.py:218-222` — Current darwin guard (raises RuntimeError)
- `src/invisible_playwright/launcher.py:289-301` — `_resolve_headless()` flow
- `src/invisible_playwright/launcher.py:258` — `virtual_display` pref flag (Windows-only)
- `src/invisible_playwright/async_api.py:169-175` — Async `_resolve_headless()` (mirrors sync)
- `pyproject.toml:27` — Platform-conditional dependency (pywin32 pattern to follow)
- `tests/test_headless.py` — 10 existing headless tests
- `tests/test_e2e.py` — 7 headless-related e2e tests
- Bug 1375585 — WebGL disabled in Firefox's native headless mode
- Chromium `virtual_display_mac_util.mm` — CGVirtualDisplay reference implementation

## Answered Open Questions (Follow-up 2026-05-14T14:51Z)

### Q1: Does CGVirtualDisplay work from Python via PyObjC?

**Yes, confirmed working.** Tested live on macOS 26.4.1 (Tahoe, Apple Silicon).

All four classes are ObjC classes (not C functions) and are accessible after loading the CoreGraphics bundle:

```python
import objc
objc.loadBundle('CoreGraphics', globals(),
                '/System/Library/Frameworks/CoreGraphics.framework')

CGVirtualDisplay           = objc.lookUpClass('CGVirtualDisplay')
CGVirtualDisplayDescriptor = objc.lookUpClass('CGVirtualDisplayDescriptor')
CGVirtualDisplayMode       = objc.lookUpClass('CGVirtualDisplayMode')
CGVirtualDisplaySettings   = objc.lookUpClass('CGVirtualDisplaySettings')
```

`objc.loadBundle()` is required first — without it, the classes are not registered in the ObjC runtime. No ctypes needed.

**Complete working lifecycle:**

```python
import objc
import Quartz

objc.loadBundle('CoreGraphics', globals(),
                '/System/Library/Frameworks/CoreGraphics.framework')

CGVirtualDisplay           = objc.lookUpClass('CGVirtualDisplay')
CGVirtualDisplayDescriptor = objc.lookUpClass('CGVirtualDisplayDescriptor')
CGVirtualDisplayMode       = objc.lookUpClass('CGVirtualDisplayMode')
CGVirtualDisplaySettings   = objc.lookUpClass('CGVirtualDisplaySettings')

# Create descriptor
desc = CGVirtualDisplayDescriptor.alloc().init()
desc.setName_('InvisiblePlaywrightVD')
desc.setMaxPixelsWide_(1920)
desc.setMaxPixelsHigh_(1080)
desc.setSizeInMillimeters_((800, 450))
desc.setVendorID_(0x1234)
desc.setProductID_(0x5678)
desc.setSerialNum_(1)

# Create virtual display
display = CGVirtualDisplay.alloc().initWithDescriptor_(desc)
display_id = display.displayID()  # e.g. 33, 34, 35...

# Create mode and settings
mode = CGVirtualDisplayMode.alloc().initWithWidth_height_refreshRate_(1920, 1080, 60.0)
settings = CGVirtualDisplaySettings.alloc().init()
settings.setModes_([mode])
settings.setHiDPI_(0)

# Apply
result = display.applySettings_(settings)  # returns True on success

# Verify
print(Quartz.CGDisplayPixelsWide(display_id))   # 1920
print(Quartz.CGDisplayPixelsHigh(display_id))    # 1080

# Teardown: release the display object (or let GC collect it)
```

**Dependency:** `pyobjc-framework-Quartz` (pulls in `pyobjc-core` + `pyobjc-framework-Cocoa`). PyObjC does not provide typed wrappers for these private classes — access is via the generic ObjC bridge.

**Known gotchas:**
- Display is **process-local**: destroyed when the `CGVirtualDisplay` Python object is garbage collected. Must keep a strong reference.
- `CGDisplayIsOnline()` and `CGDisplayIsActive()` return 0 for the virtual display, but it does appear in `NSScreen.screens` and gets a valid `CGDirectDisplayID`.
- `setHiDPI_(0)` may be ignored — display object may report hiDPI: 2 regardless.
- No existing Python projects use CGVirtualDisplay — this would be novel usage. All known implementations are Swift, ObjC, or Rust.

**Sources:** [KhaosT/CGVirtualDisplay](https://github.com/KhaosT/CGVirtualDisplay), [Stengo/DeskPad](https://github.com/Stengo/DeskPad), [phracker/MacOSX-SDKs](https://github.com/phracker/MacOSX-SDKs)

### Q2: How does Firefox target the virtual display?

**macOS has no equivalent of Linux's `DISPLAY=:99` or Windows' `SetThreadDesktop`.** The macOS WindowServer is a single compositor managing all displays in a unified global coordinate space. A window belongs to whichever display's coordinate rectangle it overlaps most.

**Two approaches for routing Firefox windows to the virtual display:**

**Approach A — Make virtual display primary (no extra permissions):**
Use `CGConfigureDisplayOrigin` to place the virtual display at origin (0,0), making it the primary display. New windows default to the primary display (the one with the menu bar). Firefox windows open there automatically. Restore the original primary after Firefox opens.

```python
import ctypes
cg = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
config = ctypes.c_void_p()
cg.CGBeginDisplayConfiguration(ctypes.byref(config))
cg.CGConfigureDisplayOrigin(config, virtual_display_id, 0, 0)
cg.CGCompleteDisplayConfiguration(config, 1)  # kCGConfigurePermanently
```

Pros: no extra permissions, all windows go there automatically.
Cons: briefly disrupts the user's display arrangement.

**Approach B — Move windows after creation (Accessibility API):**
Use `AXUIElement` to set `kAXPositionAttribute` on Firefox's windows to coordinates within the virtual display's bounds.

Pros: no disruption to user's display arrangement.
Cons: requires Accessibility permissions (System Preferences > Privacy > Accessibility), timing-sensitive (must wait for window to appear).

**Approach B is better for invisible_playwright** — it avoids disrupting the user's physical display arrangement and matches the spirit of "invisible" operation. Accessibility permissions are a one-time setup.

**How Chromium uses CGVirtualDisplay:** Chromium's `VirtualDisplayUtilMac` does NOT move windows to the virtual display. They use it only for multi-display testing (verifying display add/remove/resize detection). Not directly applicable to this use case.

**Sources:** [Chromium virtual_display_util_mac.mm](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/display/mac/test/virtual_display_util_mac.mm), [Apple CGConfigureDisplayOrigin docs](https://developer.apple.com/documentation/coregraphics/cgconfiguredisplayorigin)

### Q3: macOS version gating

**Decision: require macOS 14+ (Sonoma).** macOS < 14 is not supported.

The API actually exists since macOS 11 (Big Sur) — the previous "macOS 14+" claim was too conservative. Evidence:
- KhaosT/CGVirtualDisplay: `MACOSX_DEPLOYMENT_TARGET = 11.1` (Big Sur, Feb 2021)
- Chromium: minimum deployment target macOS 12 (Monterey), no `@available` guards on core API
- macOS SDK headers: classes exported as `objc-classes` since macOS 10.15 TBD files
- Only addition in 13.3: optional `transferFunction` property on `CGVirtualDisplayMode`

However, per project decision, the implementation will **require macOS 14+ and refuse on older versions** with a clear error message. Runtime check: `objc.lookUpClass('CGVirtualDisplay')` returns `None` if the class is unavailable.

**Sources:** [KhaosT/CGVirtualDisplay](https://github.com/KhaosT/CGVirtualDisplay), [Chromium mac_sdk.gni](https://chromium.googlesource.com/chromium/src/+/main/build/config/mac/mac_sdk.gni), [hughbe/macOS-iOS-headers](https://github.com/hughbe/macOS-iOS-headers)

### Q4: Does CGVirtualDisplay solve the Aqua session requirement for SSH/CI?

**No.** CGVirtualDisplay is NOT the macOS equivalent of Xvfb.

- **Xvfb IS the display server** — it creates a rendering environment from nothing.
- **CGVirtualDisplay adds a virtual monitor to an already-running WindowServer** — it cannot bootstrap WindowServer.
- **WindowServer only runs after a user logs in via the GUI** (Aqua session). SSH sessions do not create Aqua sessions. `SessionGetInfo()` reports `sessionHasGraphicAccess = NO` in SSH.
- **No public API exists to start a GUI login session programmatically.**

**How CI systems handle this:**

| Environment | Solution |
|-------------|----------|
| GitHub-hosted macOS runners | Auto-login pre-configured. WindowServer running. Works out of the box. |
| Self-hosted (AWS EC2 Mac, Mac mini farms) | Must configure auto-login: `/Library/Preferences/com.apple.loginwindow.plist` with `autoLoginUser` + `/etc/kcpassword`. CI runner must be a LaunchAgent (not LaunchDaemon). |
| SSH-only access | Not possible for headed Firefox. Must enable auto-login + VNC, or accept headed-only in GUI sessions. |

**Implication for invisible_playwright:** `headless=True` on macOS requires an active Aqua session (logged-in GUI user with WindowServer running). The error message should document this requirement. GitHub Actions macOS runners satisfy this automatically.

**Sources:** [Accessing macOS GUI in Automation Contexts](https://aahlenst.dev/blog/accessing-the-macos-gui-in-automation-contexts/), [Apple Developer Forums: GUI services](https://developer.apple.com/forums/thread/765060), [Apple Developer Forums: headless build server](https://developer.apple.com/forums/thread/737381)

## Remaining Open Questions

- Does Firefox correctly render to a CGVirtualDisplay that reports `CGDisplayIsActive() = 0`? Needs empirical testing with an actual Firefox launch.
- Which window-targeting approach works better in practice — make primary (Approach A) or Accessibility API move (Approach B)? Needs empirical testing.
- What Accessibility permissions does the Python process need, and how should the error message guide the user? (`tccutil` or System Preferences path)
- Does `IOPMAssertionCreateWithName(kIOPMAssertionTypeNoDisplaySleep)` need to be called to prevent the virtual display from sleeping? (Chromium does this.)

## Spike Results (Phase 1)

Tested on macOS 26.4.1 (Tahoe, Apple Silicon) with `pyobjc-framework-Quartz` installed and an active Aqua session. Date: 2026-05-14.

### Test 1: CGVirtualDisplay lifecycle — PASS

- `CGVirtualDisplay.alloc().initWithDescriptor_(desc)` succeeds, returns a valid object
- `displayID()` returns a monotonically incrementing integer (38, 41, etc.)
- `applySettings_()` returns `True` with 1920x1080 @ 60 Hz, `hiDPI=0`
- `CGDisplayPixelsWide/High` correctly reports 1920x1080
- **`CGDisplayIsOnline()` returns 0; `CGDisplayIsActive()` returns 0**
- **Virtual display does NOT appear in `CGGetOnlineDisplayList` or `CGGetActiveDisplayList`** — it lives outside those enumeration APIs
- After releasing the Python object + `gc.collect()`, the displayID is gone (resolution returns 0x0)

**Design impact:** `_save_display_layout()` using `CGGetOnlineDisplayList` will correctly capture only physical displays, never the virtual one. This is the desired behavior.

### Test 2: Firefox rendering on virtual display — SKIPPED

Patched Firefox binary not cached locally. Cannot verify rendering. This remains an open question to be tested manually after `invisible-playwright fetch`.

### Test 3: Display sleep prevention — PASS

- Created virtual display, waited 30 seconds without `IOPMAssertionCreateWithName`
- Resolution still reports 1920x1080 after the wait
- **IOPMAssertion is NOT needed** — the virtual display stays valid without it

**Design impact:** No IOPMAssertion code needed in the implementation. Simplifies `start()` and `stop()`.

### Test 4: Cleanup verification — PASS

- Virtual display reports 1920x1080 while Python object is alive
- After `del display` + `gc.collect()` + 0.5s wait, resolution returns 0x0
- **Cleanup works reliably via Python GC** — no explicit destroy/release API needed

**Design impact:** `stop()` can simply set `self._display = None` and let GC handle the cleanup. Matches the plan's design.

### Test 5: Multi-monitor layout restoration — PASS

- Tested with 1 physical display at origin (0, 0)
- Created virtual display, moved it to origin (0, 0) via `CGConfigureDisplayOrigin`
- Restored all saved physical display origins via `CGConfigureDisplayOrigin`
- Final layout matches initial snapshot — zero drift on all displays

**Design impact:** The `_save_display_layout()` / `_restore_layout()` pattern works correctly. `kCGConfigureForSession=1` (session-scoped, not persisted) is the right flag.

**Note:** Only tested with a single physical display. Multi-monitor restoration (2+ physical displays) could not be verified in this environment but the code path handles it.

### Design Decisions Informed by Spike

1. **No IOPMAssertion needed** — removes complexity from the implementation
2. **`CGGetOnlineDisplayList` is correct for `_save_display_layout()`** — virtual display is excluded automatically
3. **GC-based cleanup is sufficient** — no need for explicit `CGVirtualDisplay.destroy()` or similar
4. **`CGConfigureDisplayOrigin` works on virtual displays** — even though they don't appear in online/active lists, the display configuration API accepts their display IDs
5. **Firefox rendering remains untested** — must be verified manually with the patched binary before shipping
