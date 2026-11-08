from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

playwright = sync_playwright().start()
browser = playwright.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-setuid-sandbox"]
)
page = browser.new_page(viewport={"width": 800, "height": 600})

page.goto("https://www.google.com")

class ClickData(BaseModel):
    x: int
    y: int

@app.get("/screenshot")
def get_screenshot():
    try:
        screenshot_bytes = page.screenshot(type="jpeg", quality=40)
        return Response(content=screenshot_bytes, media_type="image/jpeg")
    except Exception as e:
        return {"error": str(e)}

@app.post("/click")
def click_page(data: ClickData):
    try:
        page.mouse.click(data.x, data.y)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
