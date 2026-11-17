FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRAILS_DB=/data/trails.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY trails ./trails

# Non-root user; /data is the bind mount with the SQLite database.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin trails \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "trails.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
