FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MOSAIC_FONT_REGULAR=/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf \
    MOSAIC_FONT_BOLD=/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
CMD ["python", "main.py"]
