FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY test_dummy_tfs_mcp/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r /app/requirements.txt

COPY test_dummy_tfs_mcp /app/test_dummy_tfs_mcp
COPY docs/dummy_contexts.json /app/docs/dummy_contexts.json

WORKDIR /app/test_dummy_tfs_mcp

EXPOSE 3010

CMD ["python", "main.py", "--port", "3010", "--topology-path", "/app/docs/dummy_contexts.json"]
