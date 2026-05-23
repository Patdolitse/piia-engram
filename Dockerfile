FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
ENV ENGRAM_TOOLS=core
CMD ["python", "-m", "piia_engram.mcp_server"]
