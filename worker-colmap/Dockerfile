# COLMAP + OpenMVS reconstruction worker (P1-4).
# Targets a CUDA GPU droplet (sizing decided at P1-5). Base image ships COLMAP
# with CUDA; we add OpenMVS (built from source) + Python + the backend package
# that carries the queue contract.
FROM colmap/colmap:latest

ENV DEBIAN_FRONTEND=noninteractive

# --- OpenMVS build dependencies -------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential \
        libboost-iostreams-dev libboost-program-options-dev \
        libboost-system-dev libboost-serialization-dev \
        libopencv-dev libcgal-dev libeigen3-dev \
        libglu1-mesa-dev freeglut3-dev \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- OpenMVS (VCGlib is a header-only dependency) --------------------------
WORKDIR /opt
RUN git clone --depth 1 https://github.com/cdcseacave/VCG.git vcglib && \
    git clone --depth 1 https://github.com/cdcseacave/openMVS.git openMVS && \
    mkdir openMVS_build && cd openMVS_build && \
    cmake ../openMVS -DCMAKE_BUILD_TYPE=Release -DVCG_ROOT=/opt/vcglib && \
    make -j"$(nproc)" && make install && \
    ldconfig
ENV PATH="/usr/local/bin/OpenMVS:${PATH}"

# --- Python: backend package (queue contract) + worker ---------------------
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip3 install --no-cache-dir --break-system-packages ./backend

COPY worker-colmap/ ./worker
WORKDIR /app/worker
ENV PYTHONPATH=/app/backend

CMD ["python3", "worker.py"]
