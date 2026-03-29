"""
FingerprintEngine — generates a unique, realistic browser fingerprint per session.
Randomizes: User-Agent, viewport, timezone, Canvas noise, WebGL vendor/renderer,
AudioContext drift, and hardware concurrency. Defeats static fingerprint detection.
"""
from __future__ import annotations

import random
import re
from typing import Optional

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 900},
    {"width": 2560, "height": 1440},
    {"width": 1280, "height": 720},
]

_TIMEZONES_BY_COUNTRY = {
    "US": ["America/New_York", "America/Chicago", "America/Los_Angeles", "America/Denver", "America/Phoenix"],
    "GB": ["Europe/London"],
    "DE": ["Europe/Berlin"],
    "FR": ["Europe/Paris"],
    "JP": ["Asia/Tokyo"],
    "AU": ["Australia/Sydney", "Australia/Melbourne"],
    "CA": ["America/Toronto", "America/Vancouver"],
    "SA": ["Asia/Riyadh"],
    "AE": ["Asia/Dubai"],
    "TR": ["Europe/Istanbul"],
    "DEFAULT": ["America/New_York", "Europe/London", "Europe/Berlin", "Asia/Tokyo"],
}

_WEBGL_VENDORS = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple Inc.", "Apple GPU"),
]

_HARDWARE_CONCURRENCY = [2, 4, 6, 8, 12, 16]
_DEVICE_MEMORY = [2, 4, 8, 16]
_COLOR_DEPTHS = [24, 30]


def _extract_chrome_version(ua: str) -> Optional[str]:
    m = re.search(r"Chrome/(\d+)", ua)
    return m.group(1) if m else "134"


class FingerprintProfile:
    def __init__(
        self,
        user_agent: str,
        viewport: dict,
        timezone_id: str,
        webgl_vendor: str,
        webgl_renderer: str,
        hardware_concurrency: int,
        device_memory: int,
        color_depth: int,
        canvas_noise: float,
        audio_noise: float,
    ):
        self.user_agent = user_agent
        self.viewport = viewport
        self.timezone_id = timezone_id
        self.webgl_vendor = webgl_vendor
        self.webgl_renderer = webgl_renderer
        self.hardware_concurrency = hardware_concurrency
        self.device_memory = device_memory
        self.color_depth = color_depth
        self.canvas_noise = canvas_noise
        self.audio_noise = audio_noise

    @property
    def chrome_version(self) -> str:
        return _extract_chrome_version(self.user_agent)

    def build_init_script(self) -> str:
        """Builds fingerprint-only JS script (non-overlapping with playwright-stealth)."""
        return f"""
(() => {{
    const _nativeToString = Function.prototype.toString;
    const _patchedFns = new Map();
    const _origCall = Function.prototype.call;
    Function.prototype.toString = function() {{
        if (_patchedFns.has(this)) return _patchedFns.get(this);
        return _origCall.call(_nativeToString, this);
    }};
    _patchedFns.set(Function.prototype.toString, 'function toString() {{ [native code] }}');
    function _patchToString(fn, nativeStr) {{
        _patchedFns.set(fn, nativeStr || 'function ' + (fn.name || '') + '() {{ [native code] }}');
    }}

    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.hardware_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {self.device_memory} }});
    Object.defineProperty(screen, 'colorDepth', {{ get: () => {self.color_depth} }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: () => {self.color_depth} }});

    Object.defineProperty(window, 'outerWidth', {{ get: () => window.innerWidth }});
    Object.defineProperty(window, 'outerHeight', {{ get: () => window.innerHeight + 85 }});
    Object.defineProperty(screen, 'width', {{ get: () => {self.viewport['width']} }});
    Object.defineProperty(screen, 'height', {{ get: () => {self.viewport['height']} }});
    Object.defineProperty(screen, 'availWidth', {{ get: () => {self.viewport['width']} }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {self.viewport['height']} - 40 }});

    Object.defineProperty(document, 'hidden', {{ get: () => false }});
    Object.defineProperty(document, 'visibilityState', {{ get: () => 'visible' }});

    try {{
        const origDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'hasFocus');
        if (origDesc) {{
            Document.prototype.hasFocus = function() {{ return true; }};
            _patchToString(Document.prototype.hasFocus, 'function hasFocus() {{ [native code] }}');
        }}
    }} catch(_) {{}}

    if (!navigator.connection) {{
        Object.defineProperty(navigator, 'connection', {{
            get: () => ({{
                effectiveType: '4g', rtt: 50, downlink: 10, saveData: false,
                onchange: null,
                addEventListener: function() {{}},
                removeEventListener: function() {{}},
                dispatchEvent: function() {{ return true; }},
            }})
        }});
    }}

    const _canvasNoise = {self.canvas_noise:.6f};
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {{
            try {{
                const imgData = ctx.getImageData(0, 0, this.width, this.height);
                const d = imgData.data;
                for (let i = 0; i < Math.min(d.length, 40); i += 4) {{
                    d[i]   = Math.min(255, d[i]   + Math.floor(_canvasNoise * 3));
                    d[i+1] = Math.min(255, d[i+1] + Math.floor(_canvasNoise * 2));
                    d[i+2] = Math.min(255, d[i+2] + Math.floor(_canvasNoise * 1));
                }}
                ctx.putImageData(imgData, 0, 0);
            }} catch(_) {{}}
        }}
        return _origToDataURL.apply(this, arguments);
    }};
    _patchToString(HTMLCanvasElement.prototype.toDataURL, 'function toDataURL() {{ [native code] }}');

    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
        const imgData = _origGetImageData.apply(this, arguments);
        const d = imgData.data;
        for (let i = 0; i < Math.min(d.length, 20); i += 4) {{
            d[i] = Math.min(255, d[i] + Math.floor(_canvasNoise));
        }}
        return imgData;
    }};
    _patchToString(CanvasRenderingContext2D.prototype.getImageData, 'function getImageData() {{ [native code] }}');

    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return '{self.webgl_vendor}';
        if (param === 37446) return '{self.webgl_renderer}';
        return _getParam.call(this, param);
    }};
    _patchToString(WebGLRenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
    if (typeof WebGL2RenderingContext !== 'undefined') {{
        const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {{
            if (param === 37445) return '{self.webgl_vendor}';
            if (param === 37446) return '{self.webgl_renderer}';
            return _getParam2.call(this, param);
        }};
        _patchToString(WebGL2RenderingContext.prototype.getParameter, 'function getParameter() {{ [native code] }}');
    }}

    const _audioNoise = {self.audio_noise:.8f};
    if (typeof AudioBuffer !== 'undefined') {{
        const _origGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(channel) {{
            const data = _origGetChannelData.call(this, channel);
            for (let i = 0; i < Math.min(data.length, 100); i++) {{
                data[i] += _audioNoise * (Math.random() * 2 - 1);
            }}
            return data;
        }};
        _patchToString(AudioBuffer.prototype.getChannelData, 'function getChannelData() {{ [native code] }}');
    }}
}})();
"""


class FingerprintEngine:
    """Generates unique browser fingerprint profiles per session."""

    def generate(self, proxy_country: str = "US") -> FingerprintProfile:
        ua = random.choice(_UA_POOL)
        viewport = dict(random.choice(_VIEWPORTS))
        tz_options = _TIMEZONES_BY_COUNTRY.get(proxy_country.upper(), _TIMEZONES_BY_COUNTRY["DEFAULT"])
        timezone_id = random.choice(tz_options)
        vendor, renderer = random.choice(_WEBGL_VENDORS)
        hw = random.choice(_HARDWARE_CONCURRENCY)
        mem = random.choice(_DEVICE_MEMORY)
        depth = random.choice(_COLOR_DEPTHS)
        canvas_noise = random.uniform(0.0001, 0.0008)
        audio_noise = random.uniform(0.00001, 0.00005)

        return FingerprintProfile(
            user_agent=ua,
            viewport=viewport,
            timezone_id=timezone_id,
            webgl_vendor=vendor,
            webgl_renderer=renderer,
            hardware_concurrency=hw,
            device_memory=mem,
            color_depth=depth,
            canvas_noise=canvas_noise,
            audio_noise=audio_noise,
        )


fingerprint_engine = FingerprintEngine()
