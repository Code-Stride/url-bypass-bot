# Playwright's image already contains Chromium + every system library it needs,
# which is what makes the browser engine work on Railway.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Make sure the browser binary is present in this image.
RUN python -m playwright install chromium && \
    (apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*)

COPY . .

EXPOSE 8080
# Headful Chromium under a virtual display — required to pass Cloudflare.
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1366x768x24", "python", "server.py"]
