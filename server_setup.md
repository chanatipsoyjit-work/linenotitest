# 🚀 ติดตั้ง LINE Bot บน Server — คู่มือฉบับเต็ม

---

## ขั้นตอนที่ 1 — SSH เข้า Server

```bash
ssh root@<SERVER_IP>
```

---

## ขั้นตอนที่ 2 — สร้างโฟลเดอร์โครงการ

```bash
mkdir -p /root/sppproject/linebot/secrets /root/sppproject/linebot/image
cd /root/sppproject/linebot
```

---

## ขั้นตอนที่ 3 — สร้างไฟล์โค้ด

### 📄 `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
flask
line-bot-sdk
playwright
gunicorn
EOF
```

---

### 🐳 `dockerfile`

```bash
cat > dockerfile << 'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    wget gnupg libglib2.0-0 libnss3 libnspr4 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-thai-tlwg fonts-noto fonts-noto-cjk fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium
COPY . .
RUN mkdir -p image
EXPOSE 5000
CMD ["gunicorn", "--workers", "1", "--worker-class", "gthread", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]
EOF
```

---

### 🐳 `docker-compose.yml`

> ⚠️ **Traefik ทำงานอยู่ที่ root ของ server แล้ว** — ใช้ labels แทน ports เพื่อให้ Traefik จัดการ routing + SSL อัตโนมัติ

```bash
cat > docker-compose.yml << 'EOF'
services:
  linebot:
    build: .
    expose:
      - "5000"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.linebot.rule=Host(`n8n.srv1267151.hstgr.cloud`) && PathPrefix(`/linebot`)"
      - "traefik.http.routers.linebot.entrypoints=websecure"
      - "traefik.http.routers.linebot.tls.certresolver=mytlschallenge"
      - "traefik.http.routers.linebot.middlewares=linebot-strip"
      # strip prefix /linebot ก่อนส่งต่อ Flask → Flask เห็น /webhook และ /image/... เหมือนเดิม
      - "traefik.http.middlewares.linebot-strip.stripprefix.prefixes=/linebot"
      - "traefik.http.services.linebot.loadbalancer.server.port=5000"
    networks:
      - root_default
    secrets:
      - line_channel_access_token
      - line_channel_secret
      - app_username
      - app_password
      - login_url
      - base_url
      - branch_map
      - image_token
    environment:
      PORT: 5000
      MAX_IMAGES: 9
      PYTHONUNBUFFERED: "1"
    volumes:
      - ./image:/app/image
    restart: unless-stopped

networks:
  root_default:
    external: true

secrets:
  line_channel_access_token:
    file: ./secrets/line_channel_access_token.txt
  line_channel_secret:
    file: ./secrets/line_channel_secret.txt
  app_username:
    file: ./secrets/app_username.txt
  app_password:
    file: ./secrets/app_password.txt
  login_url:
    file: ./secrets/login_url.txt
  base_url:
    file: ./secrets/base_url.txt
  branch_map:
    file: ./secrets/branch_map.txt
  image_token:
    file: ./secrets/image_token.txt
EOF
```

---

### 🐍 `app.py`

คัดลอกจาก local machine ผ่าน SCP (รันบน Windows PowerShell):

```powershell
scp d:\antigravity\lineautonotiimage\app.py root@<SERVER_IP>:/root/sppproject/linebot/app.py
```

หรือสร้างด้วย nano แล้ววางโค้ดทั้งหมด:

```bash
nano app.py
```

---

## ขั้นตอนที่ 4 — สร้าง Secrets

### 🔑 `line_channel_access_token.txt`
```bash
echo "ใส่_LINE_Channel_Access_Token_ที่นี่" > secrets/line_channel_access_token.txt
```
> หาได้จาก LINE Developer Console → Channel → Messaging API → Channel access token

---

### 🔑 `line_channel_secret.txt`
```bash
echo "ใส่_LINE_Channel_Secret_ที่นี่" > secrets/line_channel_secret.txt
```
> หาได้จาก LINE Developer Console → Channel → Basic settings → Channel secret

---

### 🔑 `app_username.txt`
```bash
echo "ใส่_SAP_Username_ที่นี่" > secrets/app_username.txt
```

---

### 🔑 `app_password.txt`
```bash
echo "ใส่_SAP_Password_ที่นี่" > secrets/app_password.txt
```

---

### 🔑 `login_url.txt`
```bash
echo "https://greenwing-marketing.ap1.sapanalytics.cloud/sap/fpa/ui/login.html" > secrets/login_url.txt
```
> URL หน้า Login ของ SAP Analytics Cloud

---

### 🔑 `base_url.txt`
```bash
echo "https://n8n.srv1267151.hstgr.cloud/linebot" > secrets/base_url.txt
```
> URL สาธารณะของ bot — ต้องมี `/linebot` ต่อท้ายด้วย เพราะ Traefik จะ strip `/linebot` ออกเองก่อนส่งต่อ Flask

---

### 🔑 `image_token.txt`
```bash
# สร้าง random token สำหรับ sign URL รูปภาพ
openssl rand -hex 32 > secrets/image_token.txt
cat secrets/image_token.txt   # จดไว้ถ้าต้องการ
```

---

### 🔑 `branch_map.txt`
```bash
nano secrets/branch_map.txt
```

วางข้อมูล JSON นี้ (แก้ URL ให้ตรง):
```json
{
  "รายงานยอดขาย": [
    {"label": "Greenwing", "url": "https://greenwing-marketing.ap1.sapanalytics.cloud/sap/fpa/ui/tenants/001/app.html#/story&/s/8221F6014A30257D061F446CE13600BC/?resourceName=01.Capture%2520Report%2520-%2520GW&resourceType=STORY&mode=view"},
    {"label": "Big Wing",   "url": "https://greenwing-marketing.ap1.sapanalytics.cloud/sap/fpa/ui/tenants/001/app.html#/story&/s/4A89F6014A33E2DF299F3A4871A66B40/?resourceName=02.Capture%2520Report%2520-%2520BW&resourceType=STORY&mode=view"},
    {"label": "Club House", "url": "https://greenwing-marketing.ap1.sapanalytics.cloud/sap/fpa/ui/tenants/001/app.html#/story&/s/2B9F6014A3306EFAA735BC429356A70/?resourceName=03.Capture%2520Report%2520-%2520CH&resourceType=STORY&mode=view"}
  ]
}
```
> กด `Ctrl+O` → `Enter` → `Ctrl+X` เพื่อบันทึก

---

## ขั้นตอนที่ 5 — เชื่อม Traefik ด้วย Path-based Routing

> ใช้ `/linebot` เป็น path prefix บน domain เดียวกับ n8n **— ไม่ต้องเพิ่ม DNS record ใหม่**  
> Traefik จะรับ request พาธ `/linebot/*` แล้ว **strip** prefix `/linebot` ออกก่อนส่งต่อ Flask → Flask routes (`/webhook`, `/image/...`) ทำงานเหมือนเดิมทุกอย่าง

```
User/LINE → https://<N8N_HOST>/linebot/webhook
                         ↓ Traefik: strip /linebot
               Flask: POST /webhook ✔️

User → https://<N8N_HOST>/linebot/image/abc.png
                         ↓ Traefik: strip /linebot
               Flask: GET /image/abc.png ✔️
```

### 5.1 ยืนยัน hostname ของ n8n
> ✅ **ยืนยันแล้ว** — n8n ใช้ **`n8n.srv1267151.hstgr.cloud`**  
> Webhook URL สำหรับ LINE: `https://n8n.srv1267151.hstgr.cloud/linebot/webhook`

### 5.2 หา certresolver ของ Traefik
```bash
grep -i "certresolver\|certificatesresolvers" /root/docker-compose.yml | head -5
```
นำชื่อที่ได้ไปแทน `myresolver` ใน label:
```yaml
- "traefik.http.routers.linebot.tls.certresolver=myresolver"  # ← เปลี่ยนตามจริง
```

### 5.3 ตรวจสอบ network
```bash
docker network ls
```
> ✅ **ยืนยันแล้ว** — network ของ Traefik บน server นี้คือ **`root_default`** (จาก `docker network ls`)

---

## ขั้นตอนที่ 6 — รัน Container

```bash
cd /root/sppproject/linebot
docker compose up --build -d
```

ดู log:
```bash
docker logs -f linebot-linebot-1
```

---

## ขั้นตอนที่ 7 — อัปเดต LINE Webhook URL

1. เข้า [LINE Developer Console](https://developers.line.biz/)
2. เลือก Channel → Messaging API
3. ตั้ง Webhook URL: `https://n8n.srv1267151.hstgr.cloud/linebot/webhook`
4. กด **Verify** ✅

---

## ✅ ทดสอบ

```bash
# ทดสอบ webhook
curl -X POST https://linebot.srv1267151.hstgr.cloud/webhook

# ดู container
docker ps

# ดู log
docker logs lineautonotiimage-linebot-1
```

---

## 🔄 อัปเดตในอนาคต

| ต้องการทำ | คำสั่ง |
|-----------|--------|
| แก้โค้ด (app.py) | `scp app.py root@IP:/root/sppproject/linebot/` แล้ว `docker compose up --build -d` |
| เปลี่ยน token/secret | แก้ไฟล์ใน `secrets/` แล้ว `docker compose down && docker compose up -d` |
| ดู log | `docker logs -f linebot-linebot-1` |
| Restart bot | `docker compose restart` |
