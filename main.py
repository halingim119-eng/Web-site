import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright

WIDTH, HEIGHT = 640, 480
JPEG_QUALITY = 40
CAPTURE_INTERVAL = 0.08  # 초

pw = None
browser = None
page = None
startup_error = None

latest = None
frame_id = 0
capture_task = None


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
    global pw, browser, page, startup_error, capture_task
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        print("Chromium launched successfully!")
        capture_task = asyncio.create_task(capture_loop())
        try:
            await page.goto("https://www.google.com", timeout=30000, wait_until="domcontentloaded")
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
    return {"status": "ok", "browser_ready": page is not None, "frame": frame_id}


@app.get("/screenshot")
async def get_screenshot(id: Optional[int] = None):
    if not page:
        return not_ready()
    if latest is None:
        return Response(status_code=204)
    if id is not None and id == frame_id:
        return Response(status_code=204)  # 변화 없음
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
        target_url = data.url.strip()
        if not target_url.startswith(("http://", "https://")):
            target_url = "https://" + target_url
        await page.goto(target_url, timeout=15000, wait_until="domcontentloaded")
        return {"status": "success", "url": target_url}
    except Exception as e:
        return fail(e)


@app.post("/input")
async def input_text(data: InputData):
    """백스페이스 N번 + 글자 입력을 한 번에 처리"""
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