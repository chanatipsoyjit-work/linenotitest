from flask import Flask, request, abort, send_from_directory
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, ImageMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime, timezone, timedelta
import os, glob, json, time, hmac, hashlib, threading

# ==============================
# อ่านค่าจาก Docker Secrets
# ==============================
def read_secret(secret_name: str, default: str = "") -> str:
    """
    อ่าน secret จาก /run/secrets/<secret_name>
    ถ้าไม่มีไฟล์ (เช่น ตอน dev) fallback ไปใช้ ENV แทน
    """
    secret_path = f"/run/secrets/{secret_name}"
    try:
        with open(secret_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        env_key = secret_name.upper()
        value = os.getenv(env_key, default)
        if value:
            print(f"⚠️  [{secret_name}] ไม่พบ Docker Secret — ใช้ ENV แทน (dev mode)")
        else:
            print(f"❌  [{secret_name}] ไม่พบทั้ง Secret และ ENV!")
        return value

# ==============================
# โหลด Secrets ทั้งหมด
# ==============================
CHANNEL_ACCESS_TOKEN = read_secret("line_channel_access_token")
CHANNEL_SECRET       = read_secret("line_channel_secret")
APP_USERNAME         = read_secret("app_username")
APP_PASSWORD         = read_secret("app_password")
LOGIN_URL            = read_secret("login_url")
BASE_URL             = read_secret("base_url").rstrip("/")
IMAGE_TOKEN          = read_secret("image_token")

# BRANCH_MAP รองรับ 3 รูปแบบ:
#   { "keyword": "url" }
#   { "keyword": ["url1", "url2", "url3"] }
#   { "keyword": [{"label": "สาขาสยาม", "url": "..."}, ...] }
_branch_map_raw = read_secret("branch_map", "{}")
try:
    BRANCH_MAP = json.loads(_branch_map_raw)
    print(f"✅ โหลด BRANCH_MAP สำเร็จ: {list(BRANCH_MAP.keys())}")
except json.JSONDecodeError as e:
    print(f"❌ BRANCH_MAP ไม่ใช่ JSON ที่ถูกต้อง: {e}")
    BRANCH_MAP = {}

print("DEBUG ---")
print(f"CHANNEL_TOKEN  : {'✅ loaded' if CHANNEL_ACCESS_TOKEN else '❌ missing'}")
print(f"CHANNEL_SECRET : {'✅ loaded' if CHANNEL_SECRET else '❌ missing'}")
print(f"APP_USERNAME   : {'✅ loaded' if APP_USERNAME else '❌ missing'}")
print(f"APP_PASSWORD   : {'✅ loaded' if APP_PASSWORD else '❌ missing'}")
print(f"LOGIN_URL      : {'✅ loaded' if LOGIN_URL else '❌ missing'}")
print(f"BASE_URL       : {BASE_URL or '⚠️  ยังไม่ได้ตั้งค่า'}")
print(f"BRANCH_MAP     : {list(BRANCH_MAP.keys())}")
print("---")

os.makedirs("image", exist_ok=True)

app = Flask(__name__)

MAX_IMAGES = int(os.getenv("MAX_IMAGES", 9))  # เพิ่มให้พอกับหลาย keyword × หลายรูป

# ==============================
# Signed URL
# ==============================
def generate_signed_url(filename: str, expires_in: int = 600) -> str:
    """สร้าง URL ที่หมดอายุได้ (expires_in = วินาที)"""
    expires = int(time.time()) + expires_in
    message = f"{filename}:{expires}"
    signature = hmac.new(
        IMAGE_TOKEN.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return (
        f"{BASE_URL}/image/{filename}"
        f"?expires={expires}&sig={signature}"
    )

# ==============================
# ตั้งค่า Line Bot
# ==============================
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler       = WebhookHandler(CHANNEL_SECRET)

# ==============================
# Flask serve รูปจากโฟลเดอร์ image/
# ==============================
@app.route("/image/<filename>")
def serve_image(filename):
    expires = request.args.get("expires")
    sig     = request.args.get("sig")

    if not expires or not sig:
        abort(403)

    try:
        expires = int(expires)
    except ValueError:
        abort(403)

    if expires < int(time.time()):
        abort(403)

    message = f"{filename}:{expires}"
    expected_sig = hmac.new(
        IMAGE_TOKEN.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(sig, expected_sig):
        abort(403)

    response = send_from_directory("image", filename)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

# ==============================
# ลบรูปเก่า เมื่อมีไฟล์ครบ MAX_IMAGES
# ==============================
def delete_old_images_if_full() -> int:
    files = glob.glob("image/*.png") + glob.glob("image/*.jpg")
    # ไม่นับ cache.json
    if len(files) >= MAX_IMAGES:
        for f in files:
            os.remove(f)
        with cache_lock:
            save_cache({})
        print(f"🗑️  ครบ {MAX_IMAGES} ไฟล์ — ลบรูปทั้งหมด {len(files)} ไฟล์")
        return len(files)
    print(f"📁 มีรูปอยู่ {len(files)} ไฟล์ — ยังไม่ถึง {MAX_IMAGES} ยังไม่ลบ")
    return 0

def delete_all_images():
    """ลบรูปทั้งหมดในโฟลเดอร์ image/ (ยกเว้น cache.json)"""
    files = glob.glob("image/*.png") + glob.glob("image/*.jpg")
    for f in files:
        os.remove(f)
    print(f"🗑️  ลบรูปทั้งหมด {len(files)} ไฟล์ก่อนแคปรอบใหม่")

# ==============================
# normalize BRANCH_MAP entry → list of dict {label, url}
# ==============================
def normalize_entries(raw) -> list[dict]:
    """
    รองรับ 3 รูปแบบ:
      "url"                           → [{"label": "-",       "url": "url"}]
      ["url1", "url2"]                → [{"label": "รูปที่ 1", "url": ...}, ...]
      [{"label": "สาขา", "url": ...}] → ใช้ตามที่กำหนด
    """
    if isinstance(raw, str):
        return [{"label": "-", "url": raw}]

    entries = []
    for i, item in enumerate(raw, start=1):
        if isinstance(item, str):
            entries.append({"label": f"รูปที่ {i}", "url": item})
        elif isinstance(item, dict):
            entries.append({
                "label": item.get("label", f"รูปที่ {i}"),
                "url":   item.get("url", "")
            })
    return entries

# ==============================
# แคปรูปเว็บ (Login ครั้งเดียว, หลาย URL)
# ==============================
def capture_screenshots(entries: list[dict]) -> list[dict]:
    """
    รับ list ของ {"label": ..., "url": ...}
    คืน list ของ {"label": ..., "filename": ...} (filename=None ถ้า fail)
    Login ครั้งเดียว แล้ววนแคปทุก URL ใน session เดียวกัน
    """
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )
            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            # --- Login ครั้งเดียว ---
            page.goto(LOGIN_URL)
            page.wait_for_load_state("domcontentloaded")

            if "saucedemo.com" in LOGIN_URL:
                page.wait_for_selector('#user-name', state='visible', timeout=15000)
                page.locator('#user-name').fill(APP_USERNAME)
                page.wait_for_selector('#password', state='visible', timeout=15000)
                page.locator('#password').fill(APP_PASSWORD)
                page.wait_for_selector('#login-button', state='visible', timeout=15000)
                page.locator('#login-button').click()
                try:
                    page.wait_for_url("**/inventory.html", timeout=10000)
                except PlaywrightTimeout:
                    print(f"Login ล้มเหลว — URL ปัจจุบัน: {page.url}")
                    browser.close()
                    return []
            else:
                page.wait_for_selector('#j_username', state='visible', timeout=60000)
                page.locator('#j_username').fill(APP_USERNAME)
                page.wait_for_selector('#j_password', state='visible', timeout=60000)
                page.locator('#j_password').fill(APP_PASSWORD)
                page.wait_for_selector('#logOnFormSubmit', state='visible', timeout=60000)
                page.locator('#logOnFormSubmit').click()

                try:
                    page.wait_for_url("**/home", timeout=10000)
                except PlaywrightTimeout:
                    print(f"Login ล้มเหลว — URL ปัจจุบัน: {page.url}")
                    browser.close()
                    return []

            # --- วนแคปทีละ entry ---
            for i, entry in enumerate(entries, start=1):
                label = entry["label"]
                url   = entry["url"]
                try:
                    print(f"[{i}/{len(entries)}] {label} -> {url}")
                    page.goto(url)

                    if "saucedemo.com" in url:
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(1000)
                    else:
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            print("   networkidle timeout (ignored, proceeding to wait for render)")
                        page.wait_for_timeout(8000)   # รอ SAP Lumira render รอบแรก (เพิ่มเป็น 8 วิ)

                        # --- Scroll ช้าๆ 2 รอบ เพื่อ trigger lazy-load ของ SAP Lumira ---
                        for scroll_round in range(2):
                            total_height = page.evaluate("document.body.scrollHeight")
                            print(f"   scroll รอบ {scroll_round+1} — content height = {total_height}px")
                            scroll_pos = 0
                            scroll_step = 180           # step เล็กลง → trigger ได้ละเอียดขึ้น
                            while scroll_pos < total_height:
                                page.evaluate(f"window.scrollTo(0, {scroll_pos})")
                                page.wait_for_timeout(1000)   # รอนานขึ้น 1 วิ/ขั้น
                                scroll_pos += scroll_step
                            # กลับบนสุดระหว่างรอบ
                            page.evaluate("window.scrollTo(0, 0)")
                            page.wait_for_timeout(3000)

                        # รอให้ render ครบหลัง scroll รอบสุดท้าย
                        page.wait_for_timeout(8000)

                    # --- ตั้งความสูง viewport ตาม label (ค่าที่ทดสอบแล้วว่าโหลดครบ) ---
                    FIXED_HEIGHT_MAP = {
                        "greenwing":  1500,
                        "big wing":   1200,
                        "club house": 1100,
                    }
                    label_key = label.strip().lower()
                    if label_key in FIXED_HEIGHT_MAP:
                        viewport_h = FIXED_HEIGHT_MAP[label_key]
                        print(f"   ใช้ความสูงคงที่สำหรับ '{label}': {viewport_h}px")
                    else:
                        cur_height = page.evaluate(
                            "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                        )
                        viewport_h = cur_height + 200
                        print(f"   dynamic height สำหรับ '{label}': {cur_height}px + 200 = {viewport_h}px")

                    page.set_viewport_size({"width": 1920, "height": viewport_h})
                    page.wait_for_timeout(1000 if "saucedemo.com" in url else 2000)
                    if "saucedemo.com" not in url:
                        # scroll ลงล่างสุดเพื่อ trigger render ส่วนล่าง แล้วกลับบน
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(3000)
                        page.evaluate("window.scrollTo(0, 0)")
                        page.wait_for_timeout(2000)

                    # --- แคปรูป ---
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename  = f"screenshot_{timestamp}.png"
                    filepath  = os.path.join("image", filename)

                    try:
                        panel = page.locator(".sapLumiraStoryLayoutResponsiveComponentLanePanel")
                        panel.screenshot(path=filepath, timeout=2000)
                    except Exception as panel_err:
                        print(f"   panel screenshot fallback to full_page ({panel_err})")
                        page.screenshot(path=filepath, full_page=True)

                    print(f"✅ แคปสำเร็จ: {filepath}")
                    results.append({"label": label, "filename": filename})

                    # reset viewport กลับสำหรับ entry ถัดไป
                    page.set_viewport_size({"width": 1920, "height": 1080})

                except Exception as e:
                    print(f"❌ แคป '{label}' ล้มเหลว: {e}")
                    results.append({"label": label, "filename": None})

            browser.close()

    except Exception as e:
        print(f"❌ Error capture_screenshots: {e}")

    return results

# ==============================
# JSON Cache
# ==============================
CACHE_FILE = "image/cache.json"
cache_lock = threading.Lock()

def load_cache() -> dict:
    """โหลด cache จากไฟล์ JSON"""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_cache(cache: dict):
    """บันทึก cache ลงไฟล์ JSON"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def update_cache_for_keyword(keyword: str, results: list[dict]):
    """อัปเดต cache สำหรับ keyword ที่แคปสำเร็จ"""
    with cache_lock:
        cache = load_cache()
        cache[keyword] = {
            r["label"]: {
                "filename":    r["filename"],
                "captured_at": datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M")
            }
            for r in results if r["filename"]
        }
        save_cache(cache)
        print(f"💾 บันทึก cache สำหรับ '{keyword}' — {len(cache[keyword])} รูป")

# ==============================
# แคปทุก keyword แล้ว save cache
# ==============================
# lock ป้องกัน scheduler วิ่งซ้อนกัน
capture_running = threading.Lock()

def capture_and_cache_all():
    """แคปรูปทุก keyword ใน BRANCH_MAP แล้วบันทึก cache"""
    if not capture_running.acquire(blocking=False):
        print("⚠️  capture_and_cache_all กำลังรันอยู่ — ข้ามรอบนี้")
        return
    try:
        print(f"🕐 เริ่ม capture_and_cache_all — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # ล้าง cache ทั้งหมดก่อนแคปรอบใหม่ ป้องกัน filename เก่าค้างปนกับ filename ใหม่
        with cache_lock:
            save_cache({})
            print("🗑️  ล้าง cache ทั้งหมดก่อนแคปรอบใหม่")

        # ลบรูปเก่าทั้งหมดครั้งเดียวก่อนเริ่มแคปทุก keyword
        delete_old_images_if_full()

        for keyword, raw in BRANCH_MAP.items():
            entries = normalize_entries(raw)
            results = capture_screenshots(entries)
            if results:
                update_cache_for_keyword(keyword, results)
            else:
                print(f"⚠️ '{keyword}' แคปไม่ได้ — ไม่อัปเดต cache")

        print(f"✅ capture_and_cache_all เสร็จสิ้น — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    finally:
        capture_running.release()

# ==============================
# Scheduler: แคปทุก 1 ชั่วโมง
# ==============================
CAPTURE_INTERVAL_SECONDS = 3600  # 1 ชั่วโมง

def scheduler_loop():
    """วนแคปทุก 1 ชั่วโมงใน background thread"""
    # รอ 1 ชั่วโมงแล้ววน (รอบแรกแคปทันทีที่ start_scheduler เรียก)
    while True:
        time.sleep(CAPTURE_INTERVAL_SECONDS)
        print("⏰ Scheduler: ถึงเวลาแคปรูปอัตโนมัติ")
        threading.Thread(target=capture_and_cache_all, daemon=True).start()

def start_scheduler():
    """เริ่ม background scheduler และแคปรูปรอบแรกทันที"""
    print("🚀 เริ่ม Scheduler — แคปรูปครั้งแรกทันที จากนั้นทุก 1 ชั่วโมง")
    # แคปรูปรอบแรกทันที (background)
    threading.Thread(target=capture_and_cache_all, daemon=True).start()
    # วนทุก 1 ชั่วโมง (background)
    threading.Thread(target=scheduler_loop, daemon=True).start()

# ==============================
# สร้าง messages (ImageMessage + TextMessage) จาก results
# ==============================
def build_result_messages(keyword: str, results: list[dict], captured_at: str = "") -> list:
    """
    สร้าง list ของ LINE Message objects (ImageMessage + TextMessage)
    ส่งเป็นรูปภาพโดยตรง ไม่ส่งเป็น URL
    LINE API จำกัด 5 messages ต่อ 1 reply/push
    """
    messages = []
    success_count = sum(1 for r in results if r.get("filename"))
    fail_count = len(results) - success_count

    # เพิ่ม ImageMessage สำหรับแต่ละรูปที่แคปสำเร็จ (จำกัด 4 รูป เพื่อเหลือที่ให้ TextMessage สรุป)
    max_images = 4
    image_count = 0
    for r in results:
        if r.get("filename") and image_count < max_images:
            url = generate_signed_url(r["filename"], expires_in=600)
            messages.append(
                ImageMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            )
            print(f"🖼️ {r['label']}: {url}")
            image_count += 1

    # สรุปท้าย
    summary_lines = [f"📊 {keyword} ({success_count}/{len(results)} รูป)"]
    for r in results:
        if r.get("filename"):
            summary_lines.append(f"  🏪 {r['label']} ✅")
        else:
            summary_lines.append(f"  🏪 {r['label']} ❌ แคปไม่สำเร็จ")
    if captured_at:
        summary_lines.append(f"\n🕒 ข้อมูล ณ เวลา: {captured_at}")
    summary_lines.append("📌 หากต้องการเรียกดูข้อมูลใหม่ให้พิมพ์ /รายงานยอดขาย")
    messages.append(TextMessage(text="\n".join(summary_lines)))

    return messages

# ==============================
# Webhook
# ==============================
@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """LINE Verify Request (GET) → ตอบ 200 OK"""
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body      = request.get_data(as_text=True)

    # LINE verify request อาจส่ง POST เปล่าๆ มา → ตอบ 200 OK
    if not signature or not body:
        return "OK"

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    raw_text    = event.message.text.strip()
    reply_token = event.reply_token
    source_type = event.source.type

    # ── รับเฉพาะข้อความที่ขึ้นต้นด้วย / ──
    if not raw_text.startswith("/"):
        return

    cmd = raw_text[1:].strip().lower()  # ตัด / ออก แล้ว lowercase

    if source_type == "group":
        target_id = event.source.group_id
    elif source_type == "room":
        target_id = event.source.room_id
    else:
        target_id = event.source.user_id

    print(f"📌 source_type : {source_type}")
    print(f"📌 target_id   : {target_id}")
    print(f"📌 cmd         : {cmd}")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # /keyword → แสดงรายการคำสั่งทั้งหมด
        if cmd == "keyword":
            branch_list = "\n".join([f"• /{k}" for k in BRANCH_MAP.keys()])
            line_bot_api.reply_message( 
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text=f"📋 คำสั่งที่มี:\n{branch_list}"
                    )]
                )
            )
            return

        # /<keyword> ไม่ตรงใน BRANCH_MAP
        keyword = cmd
        if keyword not in BRANCH_MAP:
            branch_list = "\n".join([f"• /{k}" for k in BRANCH_MAP.keys()])
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text=f"❓ ไม่พบคำสั่ง '/{keyword}'\n\nคำสั่งที่มี:\n{branch_list}\n\nพิมพ์ /keyword เพื่อดูรายการ"
                    )]
                )
            )
            return

        # ตรวจสอบ cache
        with cache_lock:
            cache = load_cache()

        cached = cache.get(keyword, {})

        # ── Cache HIT: มีรูปแล้ว → reply ทันทีเป็นรูปภาพ (ฟรี ไม่เสีย token) ──
        if cached:
            results = [
                {"label": label, "filename": info["filename"]}
                for label, info in cached.items()
            ]
            captured_at = next(iter(cached.values()), {}).get("captured_at", "")
            messages = build_result_messages(keyword, results, captured_at)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=messages
                )
            )
            print(f"✅ Cache HIT '{keyword}' — reply รูปภาพทันที (ฟรี)")
            return

        # ── Cache MISS: ไม่มีรูป → แคปรูปสดแล้ว reply ทันที (ฟรี ไม่เสีย token) ──
        print(f"⏳ Cache MISS '{keyword}' — กำลังแคปรูปสดเพื่อ reply ทันที...")
        entries = normalize_entries(BRANCH_MAP[keyword])
        results = capture_screenshots(entries)

        if not results or all(r["filename"] is None for r in results):
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=f"❌ แคปรูปทุก URL ของ '{keyword}' ล้มเหลว")]
                )
            )
            return

        if not BASE_URL:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(
                        text="✅ แคปรูปสำเร็จ แต่ยังไม่ได้ตั้ง BASE_URL\nกรุณาเพิ่ม secret: base_url"
                    )]
                )
            )
            return

        # บันทึก cache
        update_cache_for_keyword(keyword, results)

        captured_at = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M")
        messages = build_result_messages(keyword, results, captured_at)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages
            )
        )
        print(f"✅ Cache MISS '{keyword}' — แคปรูปและ reply รูปภาพสำเร็จ (ฟรี)")

# ==============================
# เริ่ม Scheduler ระดับ module (ทำงานทั้ง gunicorn และ python โดยตรง)
# ==============================
start_scheduler()

# ==============================
# Run (python app.py โดยตรง)
# ==============================
if __name__ == "__main__":
    print("🤖 Line Bot พร้อมทำงาน!")
    print(f"   BASE_URL    : {BASE_URL or '⚠️  ยังไม่ได้ตั้งค่า'}")
    print(f"   สาขาที่รองรับ: {list(BRANCH_MAP.keys())}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)