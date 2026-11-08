from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright

pw = None
browser = None
page = None
startup_error = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pw, browser, page, startup_error
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
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        print("Chromium launched successfully!")
        try:
            await page.goto("https://www.google.com", timeout=30000)
        except Exception as e:
            print(f"Initial page load failed: {e}")
    except Exception as e:
        startup_error = str(e)
        print(f"Error launching browser: {e}")

    yield

    try:
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
)


class ClickData(BaseModel):
    x: int
    y: int


class NavigateData(BaseModel):
    url: str


def not_ready():
    msg = "Browser not ready"
    if startup_error:
        msg += ": " + startup_error[:150]
    return JSONResponse({"error": msg}, status_code=503)


@app.get("/")
async def root():
    return {"status": "ok", "browser_ready": page is not None}


@app.get("/screenshot")
async def get_screenshot():
    if not page:
        return not_ready()
    try:
        data = await page.screenshot(type="jpeg", quality=40)
        return Response(content=data, media_type="image/jpeg")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/click")
async def click_page(data: ClickData):
    if not page:
        return not_ready()
    try:
        await page.mouse.click(data.x, data.y)
        return {"status": "success"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/navigate")
async def navigate_page(data: NavigateData):
    if not page:
        return not_ready()
    try:
        target_url = data.url.strip()
        if not target_url.startswith(("http://", "https://")):
            target_url = "https://" + target_url
        await page.goto(target_url, timeout=15000)
        return {"status": "success", "url": target_url}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)