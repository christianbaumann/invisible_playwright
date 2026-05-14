# invisible_playwright

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Firefox 150.0.1](https://img.shields.io/badge/firefox-150.0.1-orange.svg)](https://www.mozilla.org/firefox/)
[![GitHub release](https://img.shields.io/github/v/release/feder-cr/invisible_playwright.svg)](https://github.com/feder-cr/invisible_playwright/releases)
[![GitHub stars](https://img.shields.io/github/stars/feder-cr/invisible_playwright.svg?style=social)](https://github.com/feder-cr/invisible_playwright/stargazers)

A patched Firefox **100% Playwright-compatible** that passes the hardest browser-fingerprint detectors in the wild.


## Results

### Google reCAPTCHA v3 - **0.90 / 1.0**

Top-tier score. Google classifies the session as "very likely a human". Most anti-detect stacks plateau around 0.3-0.7.

![reCAPTCHA score 0.90](docs/screenshots/recaptcha_score.png)

### Fingerprint Pro - **bot: not detected, VPN: false, tampering: false, dev tools: not detected**

FingerprintJS Pro's full Smart Signals battery flips every flag to "Not detected". Browser correctly identified as Firefox 150 on Windows 10. Confidence score 0.9.

![FingerprintPro not detected](docs/screenshots/fingerprintpro.png)

### CreepJS - **0 lies**, fingerprint is internally coherent

No contradictions between headless hints, spoofed values, and real rendering output. That "0 lies" is what kills most anti-detect browsers: one inconsistency (e.g. Chrome UA + Firefox WebGL) and the trust score collapses.

![CreepJS 0 lies](docs/screenshots/creepjs.png)

### BrowserLeaks WebRTC - **no public IP leak**

WebRTC srflx address is the proxy egress IP; host candidates are private LAN. The real public IP never leaks via STUN, even on pages that configure their own ICE servers. Stock Firefox leaks the real local IP via WebRTC mDNS - invisible_playwright doesn't.

![WebRTC no leaks](docs/screenshots/webrtc.png)

### bot.sannysoft.com - **all checks pass**

Every row green: WebDriver not present, Chrome-only properties absent, plugin/mime/languages arrays coherent, permissions API correct, iframe/source window checks pass.

![Sannysoft all green](docs/screenshots/sannysoft.png)

---

## Why it's powerful

**Most anti-detect browsers patch Chromium at the JavaScript level** - they override `navigator`, `WebGLRenderingContext.getParameter`, canvas APIs, and so on via injected scripts. This has two fatal problems:

1. **JS patches are detectable.** Anti-bots enumerate native function `.toString()`, check descriptor configurability, compare property enumeration order, watch for prototype mutations. Every patch leaves a fingerprint of its own. CreepJS has an entire battery of "lies detectors" built around this.
2. **Chromium itself is now suspect.** Residential-proxy bot traffic is overwhelmingly Chromium-based, so detectors weight anything Chromium-shaped as risky by default. And the parts that matter (TLS stack, renderer process) are not fully open-source in Chrome proper - forks either inherit all Chromium tells or drift in visible ways.

**invisible_playwright patches Firefox at the C++ level.** The spoofed values come back out through the normal Gecko paths - there is no JS shim, no override, no `Object.defineProperty`. **From the page's point of view, the browser is just telling the truth.** Anti-bot lie-detectors have nothing to latch onto.

invisible_playwright spoofs **all the layers that matter, together, coherently**:

| Layer | What we do | Why it matters |
|-------|-----------|-----------------|
| Navigator / hardware | C++ overrides: UA, oscpu, languages, hardwareConcurrency, deviceMemory, storage quota | Self-description coherent across every API |
| Screen / window / pointer | C++ patch: screen WxH, outerSize bound, media-query device-size, pointer/hover/touch capabilities | `screen.*`, `window.outer*`, CSS `@media (pointer: fine)` all coherent |
| CSS system colors | 40 `ui.*` Win32 palette overrides | `getComputedStyle()` on system colors matches real Windows |
| GPU / WebGL | C++ patch: vendor, renderer, extensions whitelist, integer/float params, shader precisions, readPixels noise | Matches real Windows ANGLE down to enum values |
| Canvas 2D | C++ patch: per-pixel substitution + geometry skip-mask noise + TextMetrics variance | Defeats canvas hashing and text-metrics fingerprinting |
| Fonts / DirectWrite | C++ patch: family whitelist + fabricated authoritative list + per-family width scale + DWrite settings | Font enumeration matches real Win10; canvas text hash stable |
| Audio | C++ patch: sampleRate + output latency + max channels + AnalyserNode/DynamicsCompressor noise | AudioContext fingerprints bucket users very tightly |
| Speech synthesis | C++ patch: fabricated voices list | `navigator.speechSynthesis.getVoices()` matches the spoofed OS |
| WebRTC | C++ patch (nICEr): srflx address swap + synthetic srflx fallback + private-LAN host candidates | Real public IP never leaks via STUN |
| Timezone | C++ patch: per-Realm TZ via BrowsingContext (no IPC pref races) | `Date.getTimezoneOffset()`, `Intl.DateTimeFormat` match the spoofed location |
| DevTools detection | C++ patch: `Debugger.stealthMode` + Juggler `Runtime.js` + thread actor | FP Pro `developer_tools` = Not detected even with debugger attached |
| SOCKS5 auth | C++ patch | Stock Playwright+Firefox cannot negotiate it at all |
| DNS | Routed through SOCKS proxy by default | No DNS leak when using a residential gateway |
| Mouse motion | Bezier curves inside Juggler `PageHandler.js`, ~10 ms per waypoint | Even `page.click(selector)` moves like a human |
| GPU on virtual desktop | Pref-driven workaround for FF150 alt-desktop sandbox regression | WebGL renderer populated even in headless / multi-worker mass tests |
| Fission navigation | C++ patch: `nsDocShell` + `CanonicalBrowsingContext` Juggler navigation fix | `page.goto()` reliable on FF150 across proxy edge cases |
| about:newtab race | Async wrapper sleep around `new_page()` | No "Navigation interrupted by about:newtab" on FF150 |
| Proxy reliability | Juggler `PageHandler.equalsExceptRef` split try/catch | No spurious "Invalid url" with proxies like Evomi |

Everything is driven by preferences - no hardcoded values in the binary. You change one pref, you change the spoofed value.

---

## How it compares

Commercial anti-detect browsers (Multilogin, GoLogin, AdsPower, Kameleo, Dolphin Anty, Browserbase) ship a patched Chromium and override fingerprints at the JavaScript layer. That's the ceiling - and it's a low one.

| | invisible_playwright | Multilogin / GoLogin | AdsPower / Dolphin | Browserbase |
|---|---|---|---|---|
| Engine | Firefox (open source) | Chromium fork | Chromium fork | Chromium |
| Patch depth | C++ source | JS overrides | JS overrides | JS overrides |
| `.toString()` clean | ✅ Native Gecko path | ❌ Detectable shims | ❌ Detectable shims | ❌ Detectable shims |
| Canvas / WebGL | ✅ C++ level | ⚠️ JS override | ⚠️ JS override | ⚠️ JS override |
| SOCKS5 auth | ✅ Patched | ⚠️ Varies | ⚠️ Varies | ❌ |
| Self-hosted | ✅ | ❌ SaaS | ❌ SaaS | ❌ Cloud |
| reCAPTCHA v3 score | **0.90** | ~0.3-0.6 | ~0.3-0.5 | ~0.3-0.5 |
| FP Pro - bot detected | ✅ Not detected | ❌ Detected | ❌ Detected | ❌ Detected |
| FP Pro - tampering | ✅ Not detected | ❌ Detected | ❌ Detected | ❌ Detected |
| FP Pro - VPN flag | ✅ false | ❌ true | ❌ true | ❌ true |
| CreepJS lies | ✅ 0 | ❌ multiple | ❌ multiple | ❌ multiple |

---

## Install

```bash
pip install invisible-playwright
python -m invisible_playwright fetch      # one-time ~100 MB download, SHA256-verified
```

Supported platforms: **Windows x86_64**, **Linux x86_64**, **macOS Apple Silicon** (headed mode only — headless not yet supported).

---

## Usage
### Random fingerprint per session
**100% Playwright-compatible** - sync and async, all methods, zero API changes. If you already use Playwright, switching is two lines:

```diff
- from playwright.sync_api import sync_playwright
- with sync_playwright() as p:
-     browser = p.firefox.launch()
+ from invisible_playwright import InvisiblePlaywright
+ with InvisiblePlaywright() as browser:
```

Every session gets a unique, coherent fingerprint drawn from real-world Firefox telemetry (GPU / audio / fonts / ~400 other fields) and Bezier-curve mouse motion baked into the browser itself.

**Sync**
```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

**Async**
```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    await page.click("#submit")
```

The `browser` object is a `playwright.sync_api.Browser` / `playwright.async_api.Browser` - every Playwright method works as-is.

---

### Random fingerprint per session

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright() as browser:
    page = browser.new_page()
    page.goto("https://creepjs-api.web.app")
```

Every call samples a new coherent profile. Log the seed to reproduce interesting runs:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)
    # ...
```

### Reproducible fingerprint

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # same GPU, same canvas hash, same audio context, every run
```

### Proxies

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}
with InvisiblePlaywright(proxy=proxy) as browser:
    ...
```

Schemes supported: `socks5`, `socks4`, `http`, `https`. Auth works on all of them (SOCKS5 via patched `nsProtocolProxyService.cpp`, HTTP/HTTPS via Playwright). DNS is routed through the proxy by default, no local leak.

### Pinning specific fingerprint fields

By default everything comes from `seed`. To force specific values while the rest stays seed-derived:

```python
with InvisiblePlaywright(
    seed=42,
    pin={
        "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
        "gpu.vendor":   "Google Inc. (NVIDIA)",
        "screen.width":  2560,
        "screen.height": 1440,
        "hardware.concurrency": 16,
    },
) as browser:
    ...
```

Full list of pinnable keys, how pinning interacts with the Bayesian sampler, and common patterns are in **[docs/pinning.md](docs/pinning.md)**.

---

## CLI

```bash
invisible_playwright fetch          # download the binary if missing
invisible_playwright path           # print the absolute path to the cached binary
invisible_playwright version        # wrapper and binary versions
invisible_playwright clear-cache    # remove all cached binaries
```

## License

MIT - see [LICENSE](LICENSE). The patched Firefox binary is distributed under the MPL-2.0 (Firefox upstream license). The C++ patches against mozilla-central that produce that binary are at [feder-cr/firefox-stealth](https://github.com/feder-cr/firefox-stealth).
