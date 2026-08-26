#!/usr/bin/env bash
# COLMAP + OpenMVS reconstruction: keyframe JPEGs in → textured OBJ mesh out.
# Implements the "reconstruct" half of docs/queue-contract.md. Engine internals
# live entirely here; the queue never sees any of it.
#
#   pipeline.sh <image_dir> <work_dir> <output_obj>
#
# Assumes colmap + the OpenMVS tools (InterfaceCOLMAP, DensifyPointCloud,
# ReconstructMesh, TextureMesh) are on PATH — see Dockerfile. Runs on a CUDA GPU
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

echo "[colmap] feature extraction"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --ImageReader.single_camera 1

# Frames come from a continuous orbit video → sequential matching is both faster
# and more robust than exhaustive for ordered input.
echo "[colmap] sequential matching"
colmap sequential_matcher --database_path "$DB"

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

# --- OpenMVS: densify → mesh → texture, exporting OBJ ----------------------
echo "[openmvs] interface"
InterfaceCOLMAP --working-folder "$MVS" -i "$DENSE" -o "$MVS/scene.mvs"

echo "[openmvs] densify point cloud"
DensifyPointCloud --working-folder "$MVS" -i "$MVS/scene.mvs" -o "$MVS/scene_dense.mvs"

echo "[openmvs] reconstruct mesh"
ReconstructMesh --working-folder "$MVS" -i "$MVS/scene_dense.mvs" -o "$MVS/scene_mesh.mvs"

echo "[openmvs] texture + export OBJ"
TextureMesh --working-folder "$MVS" \
  --export-type obj \
  -i "$MVS/scene_mesh.mvs" \
  -o "$MVS/scene_textured.obj"

cp "$MVS/scene_textured.obj" "$OUTPUT_OBJ"
# carry the material/texture sidecars next to the OBJ if TextureMesh emitted them
for ext in mtl png; do
  src="$MVS/scene_textured.$ext"
  [[ -f "$src" ]] && cp "$src" "$(dirname "$OUTPUT_OBJ")/" || true
done

echo "[done] mesh → $OUTPUT_OBJ"
