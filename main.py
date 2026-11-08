import asyncio
import re
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote_plus

from fastapi import FastAPI
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright

WIDTH, HEIGHT = 640, 480
JPEG_QUALITY = 40
CAPTURE_INTERVAL = 0.08

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

pw = None
browser = None
context = None
page = None
startup_error = None

latest = None
frame_id = 0
capture_task = None

YT_PATTERNS = [
    r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([\w-]{11})",
    r"youtu\.be/([\w-]{11})",
    r"(?:youtube\.com|youtube-nocookie\.com)/(?:shorts|embed|live)/([\w-]{11})",
]


def normalize_url(raw: str) -> str:
    raw = raw.strip()

    # 유튜브 링크면 임베드 주소로 변환
    for pat in YT_PATTERNS:
        m = re.search(pat, raw)
        if m:
            return (
                f"https://www.youtube-nocookie.com/embed/{m.group(1)}"
                "?autoplay=1&rel=0&playsinline=1&hl=ko"
            )

    if raw.startswith(("http://", "https://")):
        return raw

    # 주소가 아니라 검색어처럼 보이면 구글 검색
    if "." not in raw or " " in raw:
        return "https://www.google.com/search?q=" + quote_plus(raw)

    return "https://" + raw


async def capture_loop():
    global latest, frame_id
    while True:
        try:
            if page:
                data = await page.screenshot(type="jpeg", quality=JPEG_QUALITY)
                if data != latest:
                    latest = data
                    frame_id += 1
        except Exception:
            pass
        await asyncio.sleep(CAPTURE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pw, browser, context, page, startup_error, capture_task
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = await browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            user_agent=USER_AGENT,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
        )
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR','ko','en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || { runtime: {} };
            """
        )
        page = await context.new_page()
        print("Chromium launched successfully!")
        capture_task = asyncio.create_task(capture_loop())
        try:
            await page.goto(
                "https://www.google.com", timeout=30000, wait_until="domcontentloaded"
            )
        except Exception as e:
            print(f"Initial page load failed: {e}")
    except Exception as e:
        startup_error = str(e)
        print(f"Error launching browser: {e}")

    yield

    try:
        if capture_task:
            capture_task.cancel()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Frame"],
)


class ClickData(BaseModel):
    x: int
    y: int


class NavigateData(BaseModel):
    url: str


class InputData(BaseModel):
    backspace: int = 0
    text: str = ""


class KeyData(BaseModel):
    key: str


class ScrollData(BaseModel):
    dy: int


def not_ready():
    msg = "Browser not ready"
    if startup_error:
        msg += ": " + startup_error[:150]
    return JSONResponse({"error": msg}, status_code=503)


def fail(e):
    return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "browser_ready": page is not None,
        "frame": frame_id,
        "url": page.url if page else None,
    }


@app.get("/screenshot")
async def get_screenshot(id: Optional[int] = None):
    if not page:
        return not_ready()
    if latest is None:
        return Response(status_code=204)
    if id is not None and id == frame_id:
        return Response(status_code=204)
    return Response(
        content=latest,
        media_type="image/jpeg",
        headers={"X-Frame": str(frame_id), "Cache-Control": "no-store"},
    )


@app.post("/click")
async def click_page(data: ClickData):
    if not page:
        return not_ready()
    try:
        await page.mouse.click(data.x, data.y)
        return {"status": "success"}
    except Exception as e:
        return fail(e)


@app.post("/navigate")
async def navigate_page(data: NavigateData):
    if not page:
        return not_ready()
    try:
        target_url = normalize_url(data.url)
        await page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
        return {"status": "success", "url": target_url}
    except Exception as e:
        return fail(e)


@app.post("/input")
async def input_text(data: InputData):
    if not page:
        return not_ready()
    try:
        for _ in range(max(0, min(data.backspace, 100))):
            await page.keyboard.press("Backspace")
        if data.text:
            await page.keyboard.type(data.text)
        return {"status": "success"}
    except Exception as e:
        return fail(e)


@app.post("/key")
async def press_key(data: KeyData):
    if not page:
        return not_ready()
    try:
        await page.keyboard.press(data.key)
        return {"status": "success"}
    except Exception as e:
        return fail(e)


@app.post("/scroll")
async def scroll_page(data: ScrollData):
    if not page:
        return not_ready()
    try:
        await page.mouse.wheel(0, data.dy)
        return {"status": "success"}
    except Exception as e:
        return fail(e)