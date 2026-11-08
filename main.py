import asyncio
import os
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
START_URL = "https://duckduckgo.com"

# (종류, 주소) - 위에서부터 순서대로 시도. 죽은 인스턴스는 자동으로 건너뜀
# 인스턴스는 자주 바뀌니 Render 환경변수 INSTANCES로 덮어쓸 수 있음
# 예: piped|https://piped.private.coffee,invidious|https://yewtu.be
DEFAULT_INSTANCES = [
    ("invidious", "https://yewtu.be"),
    ("invidious", "https://inv.nadeko.net"),
    ("piped", "https://piped.private.coffee"),
    ("piped", "https://piped.video"),
    ("invidious", "https://invidious.nerdvpn.net"),
]


def load_instances():
    env = os.environ.get("INSTANCES", "").strip()
    if not env:
        return DEFAULT_INSTANCES
    out = []
    for item in env.split(","):
        if "|" in item:
            kind, base = item.split("|", 1)
            out.append((kind.strip().lower(), base.strip().rstrip("/")))
    return out or DEFAULT_INSTANCES


INSTANCES = load_instances()

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
last_instance = None  # 마지막으로 성공한 인스턴스 (다음에 먼저 시도)

YT_PATTERNS = [
    r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([\w-]{11})",
    r"youtu\.be/([\w-]{11})",
    r"(?:youtube\.com|youtube-nocookie\.com)/(?:shorts|embed|live)/([\w-]{11})",
]


def video_url(kind, base, vid):
    return f"{base}/watch?v={vid}" + ("&autoplay=1" if kind == "invidious" else "")


def search_url(kind, base, q):
    if kind == "piped":
        return f"{base}/results?search_query={quote_plus(q)}"
    return f"{base}/search?q={quote_plus(q)}"


def home_url(kind, base):
    return base + "/"


def build_candidates(raw: str):
    """입력값 -> 시도할 URL 목록 (순서대로)"""
    raw = raw.strip()

    # 1) 유튜브 영상 링크 -> 인스턴스로
    for pat in YT_PATTERNS:
        m = re.search(pat, raw)
        if m:
            return [video_url(k, b, m.group(1)) for k, b in ordered_instances()]

    # 2) "yt 검색어" 또는 "yt:검색어" -> 인스턴스 검색
    m = re.match(r"^(?:yt|youtube|유튜브)\s*[: ]\s*(.+)$", raw, re.I)
    if m:
        q = m.group(1)
        return [search_url(k, b, q) for k, b in ordered_instances()]

    # 3) 유튜브 메인
    if re.match(r"^(https?://)?(www\.|m\.)?youtube\.com/?$", raw):
        return [home_url(k, b) for k, b in ordered_instances()]

    # 4) 일반 주소
    if raw.startswith(("http://", "https://")):
        return [raw]

    # 5) 주소 형태(점 있고 공백 없음)
    if "." in raw and " " not in raw:
        return ["https://" + raw]

    # 6) 그 외는 DuckDuckGo 검색
    return ["https://duckduckgo.com/?q=" + quote_plus(raw)]


def ordered_instances():
    if last_instance and last_instance in INSTANCES:
        return [last_instance] + [i for i in INSTANCES if i != last_instance]
    return INSTANCES


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
        page = await context.new_page()
        print("Chromium launched successfully!")
        capture_task = asyncio.create_task(capture_loop())
        try:
            await page.goto(START_URL, timeout=30000, wait_until="domcontentloaded")
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
        "instances": [f"{k}|{b}" for k, b in INSTANCES],
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
    global last_instance
    if not page:
        return not_ready()

    candidates = build_candidates(data.url)
    last_err = None
    for url in candidates[:4]:  # 최대 4곳까지만 시도
        try:
            resp = await page.goto(url, timeout=12000, wait_until="domcontentloaded")
            if resp is not None and resp.status >= 400:
                last_err = f"{url} -> HTTP {resp.status}"
                continue
            for inst in INSTANCES:
                if url.startswith(inst[1]):
                    last_instance = inst
                    break
            return {"status": "success", "url": url}
        except Exception as e:
            last_err = f"{url} -> {e}"
            continue
    return JSONResponse({"error": last_err or "이동 실패"}, status_code=502)


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