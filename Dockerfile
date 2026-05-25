FROM python:3.12-slim

# Non-root user for security
RUN useradd -m -u 1987 mcp
WORKDIR /app

# Install dependencies
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Data directory
RUN mkdir -p /home/mcp/.engram && chown -R mcp:mcp /home/mcp/.engram

USER mcp
ENV ENGRAM_TOOLS=all
ENV PYTHONUNBUFFERED=1

# Default: stdio transport (Glama wraps with mcp-proxy)
CMD ["python", "-m", "piia_engram.mcp_server"]
