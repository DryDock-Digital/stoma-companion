# Stoma Companion backend — FastAPI + ffmpeg (keyframe extraction).
FROM python:3.11-slim

# ffmpeg/ffprobe are required by the keyframe stage (P1-2).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first (cached layer), pinned by requirements.txt; the package itself is
# installed after the source is copied (pip install . needs app/ present).
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -c requirements.txt \
        fastapi "uvicorn[standard]" pydantic pydantic-settings python-multipart supabase

COPY app ./app
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
