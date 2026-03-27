"""
Debug script: Test ONLY reaching the signup page and bypassing Cloudflare.
Does NOT run the full registration flow.

Outputs:
  - Verbose step-by-step console logs
  - debug/error_screenshot.png  on any failure
  - debug/page_source.html      on any failure
  - debug/success_screenshot.png if signup page is reached

Usage:
    python3.11 bot_system/debug_navigation.py https://chatgpt.com
"""

import asyncio
import os
import sys
import time
import shutil

SITE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://chatgpt.com"

DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;
    window.navigator.chrome = {
        runtime: { onConnect: undefined, onMessage: undefined, id: undefined },
        loadTimes: function(){ return {}; },
        csi: function(){ return {}; },
        app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
    };
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const p = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            p.refresh = () => {};
            return p;
        },
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => {
            const m = [
                { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            ];
            m.refresh = () => {};
            return m;
        },
    });
    const oq = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) => (
        p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : oq(p)
    );
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }),
    });
    const gp = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return gp.call(this, p);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const gp2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Inc.';
            if (p === 37446) return 'Intel Iris OpenGL Engine';
            return gp2.call(this, p);
        };
    }
    Object.defineProperty(document, 'hidden', { get: () => false });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
    window.Notification = window.Notification || { permission: 'default' };
}
"""

CF_PHRASES = [
    "checking your browser", "verify you are human", "performing security",
    "just a moment", "please wait", "enable javascript",
    "checking if the site", "attention required", "one more step",
    "security check",
]

SIGNUP_BUTTON_TEXTS = [
    "sign up for free", "sign up", "signup", "register",
    "create account", "get started",
]


def log_step(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def save_debug_artifacts(page, prefix="error"):
    try:
        screenshot_path = os.path.join(DEBUG_DIR, f"{prefix}_screenshot.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        log_step(f"Screenshot saved: {screenshot_path}")
    except Exception as e:
        log_step(f"FAILED to save screenshot: {e}")

    try:
        html = await page.content()
        html_path = os.path.join(DEBUG_DIR, f"{prefix}_page_source.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        log_step(f"HTML source saved: {html_path} ({len(html)} bytes)")
    except Exception as e:
        log_step(f"FAILED to save HTML: {e}")


async def check_cloudflare(page, max_wait=25.0) -> bool:
    log_step("[Checking for Cloudflare challenge...]")
    elapsed = 0.0
    interval = 2.0
    while elapsed < max_wait:
        try:
            body_text = (await page.inner_text("body")).lower()
        except Exception:
            body_text = ""

        is_cf = any(phrase in body_text for phrase in CF_PHRASES)
        if not is_cf:
            log_step(f"[Cloudflare cleared] (after {elapsed:.1f}s)")
            return True

        matched = [p for p in CF_PHRASES if p in body_text]
        log_step(f"[Cloudflare DETECTED] phrases={matched} | waiting... ({elapsed:.1f}s / {max_wait}s)")

        if elapsed == 0:
            await save_debug_artifacts(page, prefix="cloudflare")

        await asyncio.sleep(interval)
        elapsed += interval

    log_step(f"[Cloudflare NOT cleared after {max_wait}s] FAIL")
    await save_debug_artifacts(page, prefix="cloudflare_timeout")
    return False


async def find_signup_form(page) -> bool:
    log_step("[Looking for signup form on current page...]")

    email_selectors = [
        'input[type="email"]',
        'input[name*="email"]',
        'input[placeholder*="email" i]',
        'input[autocomplete="email"]',
    ]
    for sel in email_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=500):
                log_step(f"[FOUND email input] selector={sel}")
                return True
        except Exception:
            pass

    password_sel = 'input[type="password"]'
    try:
        pw = page.locator(password_sel).first
        if await pw.is_visible(timeout=500):
            log_step(f"[FOUND password input] (login/signup form present)")
            return True
    except Exception:
        pass

    log_step("[No email/password inputs found on this page]")
    return False


async def click_signup_button(page) -> bool:
    log_step("[Searching for signup button...]")

    log_step("[Step A: Waiting 5s extra for React hydration...]")
    await asyncio.sleep(5)

    log_step("[Step B: Trying JS dispatchEvent click on data-testid=signup-button...]")
    try:
        before_url = page.url
        clicked = await page.evaluate("""() => {
            const btn = document.querySelector('[data-testid="signup-button"]');
            if (!btn) return 'NOT_FOUND';
            btn.click();
            return 'CLICKED';
        }""")
        log_step(f"[JS click result] {clicked}")
        await asyncio.sleep(3)
        if page.url != before_url:
            log_step(f"[URL changed after JS click] URL={page.url}")
            return True
        log_step(f"[URL unchanged after JS click] URL={page.url}")
    except Exception as e:
        log_step(f"[JS click error] {e}")

    log_step("[Step C: Trying Playwright .click() with force=True on button...]")
    try:
        btn = page.locator('[data-testid="signup-button"]').first
        if await btn.is_visible(timeout=1000):
            before_url = page.url
            await btn.click(force=True)
            log_step("[Force-clicked signup button]")
            await asyncio.sleep(5)
            if page.url != before_url:
                log_step(f"[URL changed after force click] URL={page.url}")
                return True
            log_step(f"[URL unchanged after force click] URL={page.url}")

            pages = page.context.pages
            log_step(f"[Open pages after force click] count={len(pages)}")
            for i, p in enumerate(pages):
                log_step(f"  page[{i}]: URL={p.url}")
    except Exception as e:
        log_step(f"[Force click error] {e}")

    log_step("[Step D: Trying mouse click at button coordinates...]")
    try:
        btn = page.locator('[data-testid="signup-button"]').first
        box = await btn.bounding_box()
        if box:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            log_step(f"[Button bounding box] x={box['x']:.0f} y={box['y']:.0f} w={box['width']:.0f} h={box['height']:.0f}")
            before_url = page.url
            await page.mouse.click(cx, cy)
            log_step(f"[Mouse clicked at ({cx:.0f}, {cy:.0f})]")
            await asyncio.sleep(5)
            if page.url != before_url:
                log_step(f"[URL changed after mouse click] URL={page.url}")
                return True
            log_step(f"[URL unchanged after mouse click] URL={page.url}")
    except Exception as e:
        log_step(f"[Mouse click error] {e}")

    log_step("[Step E: Checking all event listeners on signup button...]")
    try:
        listener_info = await page.evaluate("""() => {
            const btn = document.querySelector('[data-testid="signup-button"]');
            if (!btn) return 'NOT_FOUND';
            const info = {
                tagName: btn.tagName,
                className: btn.className.substring(0, 100),
                innerHTML: btn.innerHTML.substring(0, 200),
                parentTag: btn.parentElement ? btn.parentElement.tagName : 'none',
                parentHref: btn.parentElement ? (btn.parentElement.href || btn.parentElement.getAttribute('href') || 'none') : 'none',
                ancestors: [],
            };
            let el = btn.parentElement;
            for (let i = 0; i < 5 && el; i++) {
                info.ancestors.push({
                    tag: el.tagName,
                    href: el.href || el.getAttribute('href') || 'none',
                    dataTestid: el.getAttribute('data-testid') || 'none',
                    onclick: el.getAttribute('onclick') || 'none',
                });
                el = el.parentElement;
            }
            return JSON.stringify(info, null, 2);
        }""")
        log_step(f"[Button info]\n{listener_info}")
    except Exception as e:
        log_step(f"[Button info error] {e}")

    log_step("[Step F: Trying direct navigation to auth0.openai.com signup...]")
    auth_urls = [
        "https://auth0.openai.com/u/signup/identifier",
        "https://auth.openai.com/authorize?client_id=DRivsnm2Mu42T3KOpqdtwB3NYviHYzwD&audience=https%3A%2F%2Fapi.openai.com%2Fv1&redirect_uri=https%3A%2F%2Fchatgpt.com%2Fapi%2Fauth%2Fcallback%2Flogin-web&scope=openid+email+profile+offline_access+model.request+model.read+organization.read+organization.write&response_type=code&response_mode=query&state=signup&code_challenge_method=S256&prompt=login",
    ]
    for auth_url in auth_urls:
        log_step(f"[Trying] {auth_url[:80]}...")
        try:
            resp = await page.goto(auth_url, timeout=15000, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            log_step(f"[Response] status={status} URL={page.url}")
            await asyncio.sleep(2)

            has_form = await find_signup_form(page)
            if has_form:
                log_step("[FOUND form via direct auth URL]")
                return True

            log_step("[No form on auth URL, saving debug artifacts...]")
            await save_debug_artifacts(page, prefix="auth_direct")
        except Exception as e:
            log_step(f"[Auth URL error] {e}")

    log_step("[All signup button attempts FAILED]")
    return False


async def find_signup_link(page) -> bool:
    log_step("[Searching for signup links (a[href])...]")
    link_patterns = [
        'a[href*="register"]', 'a[href*="signup"]', 'a[href*="sign-up"]',
        'a[href*="join"]', 'a[href*="create-account"]',
    ]
    for sel in link_patterns:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=500):
                href = await loc.get_attribute("href") or ""
                text = (await loc.inner_text()).strip()
                log_step(f'[FOUND signup link] sel={sel} href="{href}" text="{text}" | clicking...')
                await loc.click()
                await asyncio.sleep(3)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                log_step(f"[After link click] URL={page.url}")
                return True
        except Exception:
            continue

    log_step("[No signup links found]")
    return False


async def run_test():
    log_step("=" * 60)
    log_step(f"DEBUG NAVIGATION TEST for: {SITE_URL}")
    log_step("=" * 60)

    from playwright.async_api import async_playwright
    from urllib.parse import urlparse

    parsed = urlparse(SITE_URL)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    chromium_path = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
    log_step(f"[Chromium path] {chromium_path}")

    pw = await async_playwright().start()
    log_step("[Playwright started]")

    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--disable-infobars",
        "--window-size=1920,1080",
        "--start-maximized",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    log_step(f"[Launch args] {launch_args}")

    launch_opts = {"headless": True, "args": launch_args}
    if chromium_path:
        launch_opts["executable_path"] = chromium_path

    browser = await pw.chromium.launch(**launch_opts)
    log_step("[Browser launched] headless=True")

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        java_script_enabled=True,
        bypass_csp=True,
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "sec-ch-ua": '"Google Chrome";v="134", "Chromium";v="134", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    log_step("[Browser context created]")

    await context.add_init_script(STEALTH_JS)
    log_step("[Stealth JS injected]")

    page = await context.new_page()
    log_step("[New page opened]")

    log_step(f"[Attempting to load URL] {base_url}")
    t0 = time.time()
    try:
        resp = await page.goto(base_url, timeout=20_000, wait_until="domcontentloaded")
        status = resp.status if resp else 0
        elapsed_ms = int((time.time() - t0) * 1000)
        log_step(f"[Page loaded] status={status} | time={elapsed_ms}ms | URL={page.url}")
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        log_step(f"[Page load FAILED] error={e} | time={elapsed_ms}ms")
        await save_debug_artifacts(page, prefix="load_error")
        await browser.close()
        await pw.stop()
        return

    if status in (403, 503):
        log_step(f"[HTTP {status}] Likely Cloudflare block")
        cf_ok = await check_cloudflare(page, max_wait=25)
        if not cf_ok:
            log_step("[RESULT: FAIL] Could not bypass Cloudflare")
            await save_debug_artifacts(page, prefix="error")
            await browser.close()
            await pw.stop()
            return
    elif status >= 400:
        log_step(f"[HTTP ERROR] status={status}")
        await save_debug_artifacts(page, prefix="error")
        await browser.close()
        await pw.stop()
        return

    cf_ok = await check_cloudflare(page, max_wait=15)
    if not cf_ok:
        log_step("[RESULT: FAIL] Cloudflare blocked after initial load")
        await save_debug_artifacts(page, prefix="error")
        await browser.close()
        await pw.stop()
        return

    log_step("[Waiting for SPA to settle...]")
    await asyncio.sleep(2)
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        log_step("[Network idle reached]")
    except Exception:
        log_step("[Network idle timeout - continuing anyway]")

    log_step(f"[Current URL] {page.url}")
    log_step(f"[Page title] {await page.title()}")

    await save_debug_artifacts(page, prefix="homepage")

    if await find_signup_form(page):
        log_step("[RESULT: SUCCESS] Signup form found directly on page")
        await save_debug_artifacts(page, prefix="success")
        await browser.close()
        await pw.stop()
        return

    if await click_signup_button(page):
        cf_ok2 = await check_cloudflare(page, max_wait=15)
        if not cf_ok2:
            log_step("[RESULT: FAIL] Cloudflare blocked after clicking signup")
            await save_debug_artifacts(page, prefix="error")
            await browser.close()
            await pw.stop()
            return

        await asyncio.sleep(2)
        log_step(f"[After signup button] URL={page.url}")
        await save_debug_artifacts(page, prefix="after_signup_click")

        if await find_signup_form(page):
            log_step("[RESULT: SUCCESS] Signup form found after clicking button")
            await save_debug_artifacts(page, prefix="success")
            await browser.close()
            await pw.stop()
            return
        else:
            log_step("[Signup form NOT found after button click]")

    if await find_signup_link(page):
        cf_ok3 = await check_cloudflare(page, max_wait=15)
        if not cf_ok3:
            log_step("[RESULT: FAIL] Cloudflare blocked after clicking link")
            await save_debug_artifacts(page, prefix="error")
            await browser.close()
            await pw.stop()
            return

        await asyncio.sleep(2)
        log_step(f"[After signup link] URL={page.url}")

        if await find_signup_form(page):
            log_step("[RESULT: SUCCESS] Signup form found after clicking link")
            await save_debug_artifacts(page, prefix="success")
            await browser.close()
            await pw.stop()
            return

    log_step("[RESULT: FAIL] Could not reach signup form")
    await save_debug_artifacts(page, prefix="error")

    log_step("[Dumping all visible text on page (first 500 chars)...]")
    try:
        body_text = await page.inner_text("body")
        print(body_text[:500], flush=True)
    except Exception as e:
        log_step(f"[Could not read body text] {e}")

    log_step("[Listing all visible buttons...]")
    try:
        buttons = await page.query_selector_all("button")
        for i, btn in enumerate(buttons):
            try:
                if await btn.is_visible():
                    text = (await btn.inner_text()).strip()
                    log_step(f"  button[{i}]: \"{text}\"")
            except Exception:
                pass
    except Exception as e:
        log_step(f"[Could not list buttons] {e}")

    log_step("[Listing all visible links...]")
    try:
        links = await page.query_selector_all("a")
        for i, link in enumerate(links[:20]):
            try:
                if await link.is_visible():
                    href = await link.get_attribute("href") or ""
                    text = (await link.inner_text()).strip()
                    log_step(f'  a[{i}]: text="{text[:50]}" href="{href[:80]}"')
            except Exception:
                pass
    except Exception as e:
        log_step(f"[Could not list links] {e}")

    log_step("[Listing all visible inputs...]")
    try:
        inputs = await page.query_selector_all("input")
        for i, inp in enumerate(inputs):
            try:
                if await inp.is_visible():
                    inp_type = await inp.get_attribute("type") or "text"
                    name = await inp.get_attribute("name") or ""
                    placeholder = await inp.get_attribute("placeholder") or ""
                    log_step(f'  input[{i}]: type="{inp_type}" name="{name}" placeholder="{placeholder}"')
            except Exception:
                pass
    except Exception as e:
        log_step(f"[Could not list inputs] {e}")

    await browser.close()
    await pw.stop()
    log_step("=" * 60)
    log_step("DEBUG TEST COMPLETE")
    log_step("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_test())
