FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY agent/requirements.txt /app/agent/requirements.txt
RUN pip install --no-cache-dir -r /app/agent/requirements.txt

COPY agent /app/agent
COPY web /app/web

EXPOSE 8000

CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
