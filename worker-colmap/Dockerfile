# COLMAP + OpenMVS reconstruction worker (P1-4).
# Targets a CUDA GPU droplet (sizing decided at P1-5). Base image ships COLMAP
# with CUDA; we add OpenMVS (built from source) + Python + the backend package
# that carries the queue contract.
# Pinned: `latest` drifted between builds (D15). Bump deliberately.
ARG COLMAP_TAG=20260718.7459
FROM colmap/colmap:${COLMAP_TAG}

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
ARG TINYEXIF_REF=1.1.0
ARG TINYNPY_REF=v1.1
ARG POSELIB_REF=v2.0.5
ARG VCGLIB_REF=658ba36d0a5666650da6e066b4794efc5a463407  # VCG master commit; tags are too old for OpenMVS 2.1
ARG OPENMVS_REF=v2.1.0
WORKDIR /opt
# OpenMVS find_package deps: Ceres (apt) + TinyEXIF/TinyNPY/PoseLib (from source).
RUN apt-get update && apt-get install -y --no-install-recommends libtinyxml2-dev libceres-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch "$TINYEXIF_REF" https://github.com/cdcseacave/TinyEXIF.git \
    && cmake -S TinyEXIF -B TinyEXIF/build -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_DEMO=OFF -DBUILD_SHARED_LIBS=ON \
    && cmake --build TinyEXIF/build -j"$(nproc)" --target install \
    && git clone --depth 1 --branch "$TINYNPY_REF" https://github.com/cdcseacave/TinyNPY.git \
    && cmake -S TinyNPY -B TinyNPY/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
    && cmake --build TinyNPY/build -j"$(nproc)" --target install \
    && git clone --depth 1 --recursive --branch "$POSELIB_REF" https://github.com/PoseLib/PoseLib.git \
    && cmake -S PoseLib -B PoseLib/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-Wno-error" \
    && cmake --build PoseLib/build -j"$(nproc)" --target install && ldconfig
# OpenMVS pinned to the same tag as the CPU image (v2.1.0 matches the OpenCV/CGAL
# that ship with the COLMAP base). The JPEGXL sed is a no-op on v2.1.0 and kept
# only so a deliberate bump to master still builds.
# The COLMAP base image ships the CUDA *runtime* but no compiler, so OpenMVS would
# silently build CPU-only (dense stereo then takes minutes, not seconds). Install nvcc
# from the CUDA apt repo the base image already configures, matching its runtime
# version, and build OpenMVS with CUDA for the worker's GPU (CUDA_ARCH: 89 = Ada
# RTX 6000/4090, 90 = H100, 86 = A10/A40/3090).
ARG CUDA_APT_VERSION=12-9
ARG CUDA_ARCH=89
RUN apt-get update && apt-get install -y --no-install-recommends \
        cuda-nvcc-${CUDA_APT_VERSION} cuda-cudart-dev-${CUDA_APT_VERSION} \
        cuda-driver-dev-${CUDA_APT_VERSION} \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/usr/local/cuda/bin:${PATH}"

RUN git clone https://github.com/cdcseacave/VCG.git vcglib && git -C vcglib checkout --quiet "$VCGLIB_REF" && \
    git clone --depth 1 --branch "$OPENMVS_REF" https://github.com/cdcseacave/openMVS.git openMVS && \
    grep -rl IMWRITE_JPEGXL_QUALITY openMVS \
        | xargs -r sed -i 's/IMWRITE_JPEGXL_QUALITY/IMWRITE_JPEG_QUALITY/g' || true && \
    mkdir openMVS_build && cd openMVS_build && \
    cmake ../openMVS -DCMAKE_BUILD_TYPE=Release -DVCG_ROOT=/opt/vcglib \
          -DOpenMVS_USE_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \
          -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
          -DCUDA_CUDA_LIBRARY=/usr/local/cuda/lib64/stubs/libcuda.so && \
    make -j"$(nproc)" && make install && \
    ldconfig
ENV PATH="/usr/local/bin/OpenMVS:${PATH}"

# ffmpeg: the worker extracts keyframes from the video itself (one download instead
# of ~90 per-frame round trips). Separate layer so the OpenMVS build stays cached.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- Python: backend package (queue contract + measurement) + worker -------
WORKDIR /app
COPY backend/pyproject.toml backend/requirements.txt backend/
COPY backend/app backend/app
RUN pip3 install --no-cache-dir --break-system-packages -c backend/requirements.txt "./backend[measure]"
# mesh decimation for the COLMAP dense path (trimesh.simplify_quadric_decimation)
RUN pip3 install --no-cache-dir --break-system-packages fast-simplification

COPY worker-colmap/ ./worker
WORKDIR /app/worker
ENV PYTHONPATH=/app/backend
ENV RECONSTRUCT_TIMEOUT_S=900

CMD ["python3", "worker.py"]
