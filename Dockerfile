FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY claude_monitor/ claude_monitor/
COPY static/ static/
RUN pip install --no-cache-dir .

EXPOSE 19001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:19001/health')" || exit 1

CMD ["uvicorn", "claude_monitor.main:app", "--host", "0.0.0.0", "--port", "19001"]
