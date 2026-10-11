FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBRARY_DIR=/library \
    CONFIG_DIR=/config \
    PORT=8484

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY magscan ./magscan

# Unraid runs containers as nobody:users (99:100); the template passes --user 99:100.
RUN mkdir -p /config /library && chmod 777 /config
VOLUME ["/config", "/library"]
EXPOSE 8484
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8484\")}/api/status')"
CMD ["python", "-m", "magscan", "serve"]
