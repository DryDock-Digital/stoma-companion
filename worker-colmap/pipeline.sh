#!/usr/bin/env bash
# COLMAP + OpenMVS reconstruction: keyframe JPEGs in → textured OBJ mesh out.
# Implements the "reconstruct" half of docs/queue-contract.md. Engine internals
# live entirely here; the queue never sees any of it.
#
#   pipeline.sh <image_dir> <work_dir> <output_obj>
#
# Assumes colmap + the OpenMVS tools (InterfaceCOLMAP, DensifyPointCloud,
# ReconstructMesh) and python3+trimesh are on PATH — see Dockerfile. Runs on a CUDA GPU
# droplet (sizing decided at P1-5).
set -euo pipefail

IMAGE_DIR="${1:?usage: pipeline.sh <image_dir> <work_dir> <output_obj>}"
WORK_DIR="${2:?missing work_dir}"
OUTPUT_OBJ="${3:?missing output_obj}"

DB="$WORK_DIR/database.db"
SPARSE="$WORK_DIR/sparse"
DENSE="$WORK_DIR/dense"
MVS="$WORK_DIR/mvs"
mkdir -p "$SPARSE" "$DENSE" "$MVS"

# 1 on the GPU image, 0 on the CPU image (COLMAP_USE_GPU set in the Dockerfile).
USE_GPU="${COLMAP_USE_GPU:-1}"

echo "[colmap] feature extraction (gpu=$USE_GPU)"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --ImageReader.single_camera 1 \
  --SiftExtraction.use_gpu "$USE_GPU"

# Frames come from a continuous orbit video → sequential matching is both faster
# and more robust than exhaustive for ordered input.
echo "[colmap] sequential matching (gpu=$USE_GPU)"
colmap sequential_matcher --database_path "$DB" --SiftMatching.use_gpu "$USE_GPU"

echo "[colmap] sparse mapping"
colmap mapper \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --output_path "$SPARSE"

# mapper writes sparse/0 (the largest reconstructed model).
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
  --output_type COLMAP

# Export camera poses as TXT next to the mesh — the measurement stage (P1-10) uses
# them to triangulate the ArUco marker for real-world scale + orientation.
echo "[colmap] export sparse poses (TXT)"
SPARSE_TXT="$WORK_DIR/sparse_txt"
mkdir -p "$SPARSE_TXT"
colmap model_converter --input_path "$MODEL" --output_path "$SPARSE_TXT" --output_type TXT

# --- OpenMVS: densify → mesh → texture, exporting OBJ ----------------------
# OpenMVS stores image paths relative to its working folder ($MVS) as
# "images/<name>", but image_undistorter wrote the undistorted images under the
# dense workspace ($DENSE/images). Without this link DensifyPointCloud fails with
# "failed reloading image .../mvs/images/*". Point $MVS/images at the real files.
ln -sfn "$DENSE/images" "$MVS/images"

echo "[openmvs] interface"
InterfaceCOLMAP --working-folder "$MVS" -i "$DENSE" -o "$MVS/scene.mvs"

echo "[openmvs] densify point cloud"
DensifyPointCloud --working-folder "$MVS" -i "$MVS/scene.mvs" -o "$MVS/scene_dense.mvs"

echo "[openmvs] reconstruct mesh (decimate=${MESH_DECIMATE:-0.3})"
# Measurement needs geometry, not texture: skip TextureMesh (it took ~19 min of a
# 60 min CPU run on the first real video and its output is never read) and decimate
# the mesh — 650k vertices is ~10x what the base slice needs. MESH_DECIMATE=1 keeps
# every face; tune against the fixtures, never against one video.
ReconstructMesh --working-folder "$MVS" \
  --decimate "${MESH_DECIMATE:-0.3}" \
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
