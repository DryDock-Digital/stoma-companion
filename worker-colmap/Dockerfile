# COLMAP + OpenMVS reconstruction worker (P1-4).
# Targets a CUDA GPU droplet (sizing decided at P1-5). Base image ships COLMAP
# with CUDA; we add OpenMVS (built from source) + Python + the backend package
# that carries the queue contract.
FROM colmap/colmap:latest

ENV DEBIAN_FRONTEND=noninteractive

# --- OpenMVS build dependencies -------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential \
        libboost-all-dev \
        libopencv-dev libcgal-dev libeigen3-dev libnanoflann-dev \
        libpng-dev libjpeg-dev libtiff-dev \
        libglu1-mesa-dev freeglut3-dev \
        python3 python3-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*

# --- OpenMVS (VCGlib header-only; TinyEXIF external dep built from source) --
WORKDIR /opt
# OpenMVS find_package deps: Ceres (apt) + TinyEXIF/TinyNPY/PoseLib (from source).
RUN apt-get update && apt-get install -y --no-install-recommends libtinyxml2-dev libceres-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 https://github.com/cdcseacave/TinyEXIF.git \
    && cmake -S TinyEXIF -B TinyEXIF/build -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_DEMO=OFF -DBUILD_SHARED_LIBS=ON \
    && cmake --build TinyEXIF/build -j"$(nproc)" --target install \
    && git clone --depth 1 https://github.com/cdcseacave/TinyNPY.git \
    && cmake -S TinyNPY -B TinyNPY/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
    && cmake --build TinyNPY/build -j"$(nproc)" --target install \
    && git clone --depth 1 --recursive https://github.com/PoseLib/PoseLib.git \
    && cmake -S PoseLib -B PoseLib/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build PoseLib/build -j"$(nproc)" --target install && ldconfig
# OpenMVS master uses an OpenCV 4.8+ imwrite flag; map it to the JPEG equivalent
# for older OpenCV (only affects debug-image quality).
RUN git clone --depth 1 https://github.com/cdcseacave/VCG.git vcglib && \
    git clone --depth 1 https://github.com/cdcseacave/openMVS.git openMVS && \
    grep -rl IMWRITE_JPEGXL_QUALITY openMVS \
        | xargs -r sed -i 's/IMWRITE_JPEGXL_QUALITY/IMWRITE_JPEG_QUALITY/g' && \
    mkdir openMVS_build && cd openMVS_build && \
    cmake ../openMVS -DCMAKE_BUILD_TYPE=Release -DVCG_ROOT=/opt/vcglib && \
    make -j"$(nproc)" && make install && \
    ldconfig
ENV PATH="/usr/local/bin/OpenMVS:${PATH}"

# --- Python: backend package (queue contract + measurement) + worker -------
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip3 install --no-cache-dir --break-system-packages "./backend[measure]"

COPY worker-colmap/ ./worker
WORKDIR /app/worker
ENV PYTHONPATH=/app/backend

CMD ["python3", "worker.py"]
