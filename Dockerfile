# Dockerfile — alternative deployment path (also works on Railway).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects PORT automatically.
EXPOSE 8080

CMD ["python", "server.py"]
