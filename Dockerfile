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
RUN python -m playwright install chromium

COPY . .

EXPOSE 8080
CMD ["python", "server.py"]
