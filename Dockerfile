FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ROSIE_DB_PATH=/data/app.db \
    ROSIE_BRANDING=/app/branding.toml \
    ROSIE_HOME_CONFIG=/app/home.toml \
    ROSIE_LINKS=/app/links.toml

# Same UID/GID as the Pi's main user, so ./data on the host stays writable by both.
RUN groupadd -g 1000 app && useradd -u 1000 -g app -M -s /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml README.md branding.toml home.toml links.toml ./
COPY src ./src
RUN pip install .

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["uvicorn", "rosie.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
