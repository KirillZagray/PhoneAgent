FROM python:3.11-slim

WORKDIR /app

# System deps for audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

# Copy and install deps
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Copy source
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/admin/health').raise_for_status()" || exit 1

CMD ["python", "-m", "uvicorn", "phoneagent.main:app", "--host", "0.0.0.0", "--port", "8000"]