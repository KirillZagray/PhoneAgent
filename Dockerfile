FROM python:3.11-slim

WORKDIR /app

# ffmpeg — только для edge_tts (MP3 -> PCM). VoiceStudio/ElevenLabs отдают PCM сами.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

# Copy and install deps — uv.lock pinned, reproducible build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-install-project

# Copy source
COPY src/ ./src/
COPY README.md ./
RUN uv sync --frozen --no-cache

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 phoneagent && chown -R phoneagent:phoneagent /app
USER phoneagent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/admin/health').raise_for_status()" || exit 1

CMD ["python", "-m", "uvicorn", "phoneagent.main:app", "--host", "0.0.0.0", "--port", "8000"]