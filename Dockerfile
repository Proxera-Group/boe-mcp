FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Servidor MCP por stdio (sin puertos, sin claves). Caché opcional: BOE_MCP_CACHE_DIR.
ENTRYPOINT ["boe-mcp"]
