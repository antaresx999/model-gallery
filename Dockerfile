# OCI-Image für Docker/Podman – läuft auch direkt unter Incus (>= 6.3: incus launch oci-docker:...)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GALLERY_DATA=/data

RUN useradd --system --uid 1000 --home /app gallery \
 && mkdir -p /data && chown gallery /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

USER gallery
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
