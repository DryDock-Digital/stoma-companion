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
PEAK_THRESHOLD="${COLMAP_PEAK_THRESHOLD:-0.004}"  # SIFT DoG peak; default 0.0067 — lower = more features on low-texture skin/mat
INIT_MIN_INLIERS="${COLMAP_INIT_MIN_INLIERS:-50}" # mapper init pair (default 100); low-texture orbits need the slack
MIN_NUM_MATCHES="${COLMAP_MIN_NUM_MATCHES:-10}"   # mapper (default 15)
SEQ_OVERLAP="${COLMAP_SEQ_OVERLAP:-10}"        # frames come from one continuous orbit
DENSE_ENGINE="${DENSE_ENGINE:-auto}"           # colmap (CUDA patch-match) | openmvs | auto
MVS_RES_LEVEL="${MVS_RESOLUTION_LEVEL:-2}"     # 0 = full res, each level halves
MVS_VIEWS="${MVS_NUMBER_VIEWS:-4}"
MVS_MAX_RES="${MVS_MAX_RESOLUTION:-1200}"
DECIMATE="${MESH_DECIMATE:-0.3}"
MESH_MODE="${MESH_MODE:-mesh}"                 # mesh (ReconstructMesh) | points (dense cloud only)
MAX_POINTS="${MAX_POINTS:-1500000}"            # points mode: subsample cap for the artefact

# COLMAP renamed the GPU/size options between 3.x and 4.x; pick whichever this
# binary understands so one script serves the apt (3.x) and CUDA (4.x) images.
if colmap feature_extractor -h 2>&1 | grep -q -- "--FeatureExtraction.use_gpu"; then
  OPT_EXTRACT_GPU="--FeatureExtraction.use_gpu"
  OPT_EXTRACT_MAXSIZE="--FeatureExtraction.max_image_size"
else
  OPT_EXTRACT_GPU="--SiftExtraction.use_gpu"
  OPT_EXTRACT_MAXSIZE="--SiftExtraction.max_image_size"
fi
if colmap sequential_matcher -h 2>&1 | grep -q -- "--FeatureMatching.use_gpu"; then
  OPT_MATCH_GPU="--FeatureMatching.use_gpu"
else
  OPT_MATCH_GPU="--SiftMatching.use_gpu"
fi

# Per-step wall-clock, printed as "[t] <step>=<seconds>" and parsed into the job's
# diagnostics by reconstruct.py, so every run carries its own breakdown.
STEP_T0=$(date +%s.%N)
tick() { local now; now=$(date +%s.%N); awk -v a="$STEP_T0" -v b="$now" -v n="$1" 'BEGIN{printf "[t] %s=%.1f\n", n, b-a}'; STEP_T0=$now; }

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
  "$OPT_EXTRACT_GPU" "$USE_GPU" \
  "$OPT_EXTRACT_MAXSIZE" "$MAX_IMAGE_SIZE" \
  --SiftExtraction.max_num_features "$MAX_FEATURES" \
  --SiftExtraction.peak_threshold "$PEAK_THRESHOLD"

tick features
echo "[colmap] sequential matching"
colmap sequential_matcher \
  --database_path "$DB" \
  "$OPT_MATCH_GPU" "$USE_GPU" \
  --SequentialMatching.overlap "$SEQ_OVERLAP" \
  --SequentialMatching.quadratic_overlap 1 \
  --SequentialMatching.loop_detection 0

tick matching
echo "[colmap] sparse mapping"
colmap mapper \
  --database_path "$DB" \
  --image_path "$IMAGE_DIR" \
  --output_path "$SPARSE" \
  --Mapper.ba_global_max_num_iterations 25 \
  --Mapper.init_min_num_inliers "$INIT_MIN_INLIERS" \
  --Mapper.min_num_matches "$MIN_NUM_MATCHES" \
  --Mapper.multiple_models 0

tick mapper
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

tick undistort
# Poses as TXT into the work dir — reconstruct.py converts them to the engine-neutral
# poses.json (docs/queue-contract.md).
echo "[colmap] export sparse poses (TXT)"
SPARSE_TXT="$WORK_DIR/sparse_txt"
mkdir -p "$SPARSE_TXT"
colmap model_converter --input_path "$MODEL" --output_path "$SPARSE_TXT" --output_type TXT

tick poses
# --- Dense reconstruction ----------------------------------------------------
# auto → OpenMVS (built with CUDA in the GPU image, CPU in the CPU image). COLMAP's
# own patch-match is kept as an option but measured at ~24 s/frame on an RTX 6000
# Ada (two passes, 20 source views) — far too slow for the 60 s target.
if [[ "$DENSE_ENGINE" == "auto" ]]; then DENSE_ENGINE=openmvs; fi
echo "[dense] engine=$DENSE_ENGINE"

if [[ "$DENSE_ENGINE" == "colmap" ]]; then
  echo "[colmap] patch-match stereo (GPU)"
  colmap patch_match_stereo \
    --workspace_path "$DENSE" \
    --workspace_format COLMAP \
    --PatchMatchStereo.max_image_size "$MAX_IMAGE_SIZE" \
    --PatchMatchStereo.geom_consistency true \
    --PatchMatchStereo.num_iterations "${PMS_ITERATIONS:-5}" \
    --PatchMatchStereo.window_radius "${PMS_WINDOW_RADIUS:-5}"

  tick patch_match
  echo "[colmap] stereo fusion"
  colmap stereo_fusion \
    --workspace_path "$DENSE" \
    --workspace_format COLMAP \
    --input_type geometric \
    --StereoFusion.max_image_size "$MAX_IMAGE_SIZE" \
    --output_path "$DENSE/fused.ply"

  tick fusion
  echo "[colmap] poisson mesh (depth=${POISSON_DEPTH:-10}, trim=${POISSON_TRIM:-7})"
  colmap poisson_mesher \
    --input_path "$DENSE/fused.ply" \
    --output_path "$DENSE/meshed-poisson.ply" \
    --PoissonMeshing.depth "${POISSON_DEPTH:-10}" \
    --PoissonMeshing.trim "${POISSON_TRIM:-7}"

  tick poisson
  echo "[export] PLY → OBJ (decimate=$DECIMATE)"
  python3 - "$DENSE/meshed-poisson.ply" "$OUTPUT_OBJ" "$DECIMATE" <<'PY'
import sys
import trimesh

mesh = trimesh.load(sys.argv[1], force="mesh", process=False)
frac = float(sys.argv[3])
if 0 < frac < 1 and len(mesh.faces) > 200_000:
    try:
        mesh = mesh.simplify_quadric_decimation(face_count=int(len(mesh.faces) * frac))
    except BaseException as exc:  # optional dependency missing → keep full mesh
        print(f"[export] decimation skipped: {exc}")
mesh.export(sys.argv[2], file_type="obj", include_texture=False)
print(f"[export] {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")
PY
  tick export
  echo "[done] mesh → $OUTPUT_OBJ"
  exit 0
fi

# --- OpenMVS: densify → mesh, exporting OBJ (no texturing: unused, ~19 min on CPU) ---
# OpenMVS stores image paths relative to its working folder ($MVS) as "images/<name>";
# image_undistorter wrote them under $DENSE/images — point $MVS/images at the real files.
ln -sfn "$DENSE/images" "$MVS/images"

echo "[openmvs] interface"
InterfaceCOLMAP --working-folder "$MVS" -i "$DENSE" -o "$MVS/scene.mvs"

tick interface
echo "[openmvs] densify point cloud (level=$MVS_RES_LEVEL views=$MVS_VIEWS max_res=$MVS_MAX_RES)"
DensifyPointCloud --working-folder "$MVS" \
  --resolution-level "$MVS_RES_LEVEL" \
  --number-views "$MVS_VIEWS" \
  --max-resolution "$MVS_MAX_RES" \
  -i "$MVS/scene.mvs" -o "$MVS/scene_dense.mvs"

tick densify
if [[ "$MESH_MODE" == "points" ]]; then
  # Measurement works on point sections (polar outlines); skip meshing — the single
  # largest CPU step on the GPU worker (26–49 s). Export the dense cloud as a
  # vertex-only OBJ (trimesh loads it as a PointCloud).
  echo "[export] dense cloud → vertex-only OBJ (max $MAX_POINTS points)"
  python3 - "$MVS/scene_dense.ply" "$OUTPUT_OBJ" "$MAX_POINTS" <<'PY'
import sys

import numpy as np
import trimesh

cloud = trimesh.load(sys.argv[1], process=False)
pts = np.asarray(cloud.vertices, dtype=float)
cap = int(sys.argv[3])
if len(pts) > cap:
    pts = pts[np.random.default_rng(0).choice(len(pts), cap, replace=False)]
with open(sys.argv[2], "w") as fh:
    fh.write("# dense point cloud (no faces)\n")
    np.savetxt(fh, pts, fmt="v %.6f %.6f %.6f")
print(f"[export] {len(pts)} points")
PY
  tick export
  echo "[done] points → $OUTPUT_OBJ"
  exit 0
fi
echo "[openmvs] reconstruct mesh (decimate=$DECIMATE)"
ReconstructMesh --working-folder "$MVS" \
  --decimate "$DECIMATE" \
  -i "$MVS/scene_dense.mvs" -o "$MVS/scene_mesh.mvs"

tick mesh
echo "[export] PLY → OBJ"
python3 - "$MVS/scene_mesh.ply" "$OUTPUT_OBJ" <<'PY'
import sys
import trimesh

mesh = trimesh.load(sys.argv[1], force="mesh", process=False)
mesh.export(sys.argv[2], file_type="obj", include_texture=False)
print(f"[export] {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")
PY

tick export
echo "[done] mesh → $OUTPUT_OBJ"
