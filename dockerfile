# ใช้ Python 3.11
FROM python:3.11-slim

# ติดตั้ง system dependencies สำหรับ Playwright + ฟอนต์ไทย
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-thai-tlwg \
    fonts-noto \
    fonts-noto-cjk \
    fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

# กำหนด working directory
WORKDIR /app

# ติดตั้ง Python packages
COPY requirements.txt .
RUN pip install -r requirements.txt

# ติดตั้ง Chromium สำหรับ Playwright
RUN playwright install chromium

# คัดลอกโค้ดทั้งหมด
COPY . .

# สร้างโฟลเดอร์เก็บรูป
RUN mkdir -p image

# เปิด port
EXPOSE 5000

# รัน Bot
CMD ["gunicorn", "--workers", "1", "--worker-class", "gthread", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]