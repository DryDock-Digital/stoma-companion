#!/bin/bash
# Reconstruction benchmark on the GPU host: per-step COLMAP seconds for several settings.
# Preserved from /opt/stoma/bench.sh on the GPU box (2026-08-26 timing runs); expects
# the clip at /bench/bench.mov and colmap on PATH (run inside the stoma-worker image).
set -u
B=/bench; mkdir -p $B; cd $B
mkdir -p all; [ "$(ls all 2>/dev/null | wc -l)" -gt 8 ] || ffmpeg -y -v error -i /bench/bench.mov -vf "fps=1/0.35,scale='min(2560,iw)':'min(2560,ih)':force_original_aspect_ratio=decrease" -q:v 2 -start_number 0 /bench/all/frame_%05d.jpg
N_ALL=$(ls all | wc -l); echo "frames: $N_ALL"
mkdir -p half; i=0; for f in all/*.jpg; do if (( i % 2 == 0 )); then cp "$f" half/; fi; i=$((i+1)); done
t() { local s=$(date +%s.%N); "$@" >/dev/null 2>&1; local rc=$?; local e=$(date +%s.%N); awk -v a="$s" -v b="$e" 'BEGIN{printf "%.1f", b-a}'; return $rc; }
run() { # name imgdir maxsize geom
  local name=$1 img=$2 maxsz=$3 geom=$4; local W=/bench/w_$name; rm -rf $W; mkdir -p $W/sparse
  local n=$(ls $img | wc -l)
  local t1=$(t colmap feature_extractor --database_path $W/db.db --image_path $img --ImageReader.single_camera 1 --FeatureExtraction.use_gpu 1 --FeatureExtraction.max_image_size $maxsz --SiftExtraction.max_num_features 8192)
  local t2=$(t colmap sequential_matcher --database_path $W/db.db --FeatureMatching.use_gpu 1 --SequentialMatching.overlap 10 --SequentialMatching.loop_detection 0)
  local t3=$(t colmap mapper --database_path $W/db.db --image_path $img --output_path $W/sparse --Mapper.ba_global_max_num_iterations 25)
  local t4=$(t colmap image_undistorter --image_path $img --input_path $W/sparse/0 --output_path $W/dense --output_type COLMAP --max_image_size $maxsz)
  local t5=$(t colmap patch_match_stereo --workspace_path $W/dense --workspace_format COLMAP --PatchMatchStereo.max_image_size $maxsz --PatchMatchStereo.geom_consistency $geom --PatchMatchStereo.num_iterations 5)
  local itype=geometric; [[ "$geom" == "false" ]] && itype=photometric
  local t6=$(t colmap stereo_fusion --workspace_path $W/dense --workspace_format COLMAP --input_type $itype --StereoFusion.max_image_size $maxsz --output_path $W/dense/fused.ply)
  local t7=$(t colmap poisson_mesher --input_path $W/dense/fused.ply --output_path $W/dense/mesh.ply --PoissonMeshing.depth 10 --PoissonMeshing.trim 7)
  local reg=$(colmap model_analyzer --path $W/sparse/0 2>&1 | grep -oE "Registered images: [0-9]+" | grep -oE "[0-9]+$")
  echo "RESULT name=$name frames=$n maxsize=$maxsz geom=$geom | feat=$t1 match=$t2 map=$t3 undist=$t4 pms=$t5 fuse=$t6 poisson=$t7 | registered=$reg pts=$(grep -m1 -oE 'element vertex [0-9]+' $W/dense/fused.ply | grep -oE '[0-9]+$')"
}
run a_88_2560_geom  /bench/all  2560 true
run b_88_1600_geom  /bench/all  1600 true
run c_44_1600_geom  /bench/half 1600 true
run d_44_1600_photo /bench/half 1600 false
run e_44_2560_geom  /bench/half 2560 true
echo BENCH_DONE
