FROM python:3.11-slim

WORKDIR /app

# mcp[cli] kéo FastMCP + SSE transport; psycopg2 cho logging vào PostgreSQL
RUN pip install --no-cache-dir "mcp[cli]>=1.0.0" "psycopg2-binary>=2.9"

COPY server.py config.py security.py rate_limit.py event_log.py helpers.py .

EXPOSE 8001

# FastMCP SSE server mặc định bind 0.0.0.0:8000 → override bằng env hoặc args
CMD ["python", "server.py"]
