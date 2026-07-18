FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r /app/requirements.txt

COPY . /app

RUN mkdir -p /data

ENV SQLITE_PATH=/data/multi_smart.db \
    PORT=8787 \
    HOST=0.0.0.0 \
    OLLAMA_BASE_URL=http://10.1.1.89:11434 \
    PLANNER_MODEL=qwen2.5:7b-instruct \
    DEFAULT_AGENT_MODEL=qwen2.5:3b-instruct \
    SUMMARY_MODEL=qwen2.5:1.5b-instruct

EXPOSE 8787

VOLUME ["/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
