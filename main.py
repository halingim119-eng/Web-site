from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

playwright = None
browser = None
page = None

# 서버가 실행될 때 안전하게 브라우저를 켭니다.
@app.on_event("startup")
def startup_event():
    global playwright, browser, page
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.goto("https://www.google.com")
        print("Chromium launched successfully!")
    except Exception as e:
        print(f"Error launching browser: {e}")

class ClickData(BaseModel):
    x: int
    y: int

@app.get("/screenshot")
def get_screenshot():
    if not page:
        return {"error": "Browser not ready yet"}, 500
    try:
        screenshot_bytes = page.screenshot(type="jpeg", quality=40)
        return Response(content=screenshot_bytes, media_type="image/jpeg")
    except Exception as e:
        return {"error": str(e)}

@app.post("/click")
def click_page(data: ClickData):
    if not page:
        return {"error": "Browser not ready yet"}, 500
    try:
        page.mouse.click(data.x, data.y)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}
