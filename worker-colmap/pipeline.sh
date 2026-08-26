#!/usr/bin/env bash
# COLMAP + OpenMVS reconstruction: keyframe JPEGs in → decimated OBJ mesh + poses out.
# Implements the "reconstruct" half of docs/queue-contract.md. Engine internals
# live entirely here; the queue never sees any of it.
#
#   pipeline.sh <image_dir> <work_dir> <output_obj>
#
# Assumes colmap + the OpenMVS tools (InterfaceCOLMAP, DensifyPointCloud,
# ReconstructMesh) and python3+trimesh are on PATH — see the Dockerfiles.
#
# Speed knobs (env; defaults chosen by the keyframe/resolution sweep on real footage,
# recorded in decisions.md). Every run stamps the values it used into the job's
# diagnostics (reconstruct.py), so a number is never separated from its settings.
set -euo pipefail

IMAGE_DIR="${1:?usage: pipeline.sh <image_dir> <work_dir> <output_obj>}"
WORK_DIR="${2:?missing work_dir}"
OUTPUT_OBJ="${3:?missing output_obj}"

DB="$WORK_DIR/database.db"
SPARSE="$WORK_DIR/sparse"
DENSE="$WORK_DIR/dense"
MVS="$WORK_DIR/mvs"
mkdir -p "$SPARSE" "$DENSE" "$MVS"

USE_GPU="${COLMAP_USE_GPU:-1}"                 # 1 on the CUDA image, 0 on the CPU image
MAX_IMAGE_SIZE="${COLMAP_MAX_IMAGE_SIZE:-1600}" # SIFT + undistorted image longest edge
MAX_FEATURES="${COLMAP_MAX_FEATURES:-4096}"
SEQ_OVERLAP="${COLMAP_SEQ_OVERLAP:-10}"        # frames come from one continuous orbit
MVS_RES_LEVEL="${MVS_RESOLUTION_LEVEL:-2}"     # 0 = full res, each level halves
MVS_VIEWS="${MVS_NUMBER_VIEWS:-4}"
MVS_MAX_RES="${MVS_MAX_RESOLUTION:-1200}"
DECIMATE="${MESH_DECIMATE:-0.3}"

# Record which GPU (if any) this run had — surfaces on the admin run page.
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 > "$WORK_DIR/gpu.txt" || true
fi
echo "[env] gpu=$USE_GPU max_image=$MAX_IMAGE_SIZE feats=$MAX_FEATURES overlap=$SEQ_OVERLAP mvs_level=$MVS_RES_LEVEL views=$MVS_VIEWS decimate=$DECIMATE"

echo "[colmap] feature extraction"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --ImageReader.single_camera 1 \
  --SiftExtraction.use_gpu "$USE_GPU" \
  --SiftExtraction.max_image_size "$MAX_IMAGE_SIZE" \
  --SiftExtraction.max_num_features "$MAX_FEATURES"

echo "[colmap] sequential matching"
colmap sequential_matcher \
  --database_path "$DB" \
  --SiftMatching.use_gpu "$USE_GPU" \
  --SequentialMatching.overlap "$SEQ_OVERLAP" \
  --SequentialMatching.loop_detection 0

echo "[colmap] sparse mapping"
colmap mapper \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --output_path "$SPARSE" \
  --Mapper.ba_global_max_num_iterations 25

MODEL="$SPARSE/0"
if [[ ! -d "$MODEL" ]]; then
  echo "[colmap] mapping produced no model — reconstruction failed" >&2
  exit 3
fi

echo "[colmap] undistort → dense workspace"
colmap image_undistorter \
  --image_path "$IMAGE_DIR" \
  --input_path "$MODEL" \
  --output_path "$DENSE" \
  --output_type COLMAP \
  --max_image_size "$MAX_IMAGE_SIZE"

# Poses as TXT into the work dir — reconstruct.py converts them to the engine-neutral
# poses.json (docs/queue-contract.md).
echo "[colmap] export sparse poses (TXT)"
SPARSE_TXT="$WORK_DIR/sparse_txt"
mkdir -p "$SPARSE_TXT"
colmap model_converter --input_path "$MODEL" --output_path "$SPARSE_TXT" --output_type TXT

# --- OpenMVS: densify → mesh, exporting OBJ (no texturing: unused, ~19 min on CPU) ---
# OpenMVS stores image paths relative to its working folder ($MVS) as "images/<name>";
# image_undistorter wrote them under $DENSE/images — point $MVS/images at the real files.
ln -sfn "$DENSE/images" "$MVS/images"

echo "[openmvs] interface"
InterfaceCOLMAP --working-folder "$MVS" -i "$DENSE" -o "$MVS/scene.mvs"

echo "[openmvs] densify point cloud (level=$MVS_RES_LEVEL views=$MVS_VIEWS max_res=$MVS_MAX_RES)"
DensifyPointCloud --working-folder "$MVS" \
  --resolution-level "$MVS_RES_LEVEL" \
  --number-views "$MVS_VIEWS" \
  --max-resolution "$MVS_MAX_RES" \
  -i "$MVS/scene.mvs" -o "$MVS/scene_dense.mvs"

echo "[openmvs] reconstruct mesh (decimate=$DECIMATE)"
ReconstructMesh --working-folder "$MVS" \
  --decimate "$DECIMATE" \
  -i "$MVS/scene_dense.mvs" -o "$MVS/scene_mesh.mvs"

echo "[export] PLY → OBJ"
python3 - "$MVS/scene_mesh.ply" "$OUTPUT_OBJ" <<'PY'
import sys
import trimesh

mesh = trimesh.load(sys.argv[1], force="mesh", process=False)
mesh.export(sys.argv[2], file_type="obj", include_texture=False)
print(f"[export] {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")
PY

echo "[done] mesh → $OUTPUT_OBJ"
