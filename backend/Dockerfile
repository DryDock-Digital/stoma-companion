# Stoma Companion backend — FastAPI + ffmpeg (keyframe extraction).
FROM python:3.11-slim

# ffmpeg/ffprobe are required by the keyframe stage (P1-2).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
