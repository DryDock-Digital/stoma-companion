"""Admin test bench endpoints: upload with truth, list, detail, caliper patch that
recomputes deviation and syncs the verification log, G-code + CSV downloads."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import paths
from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from app.runlog import InMemoryRunStore
from app.store import InMemoryJobStore


def _client():
    store, runs = InMemoryJobStore(), InMemoryRunStore()
    app = create_app(store=store, settings=Settings(run_keyframe_worker=False), run_store=runs)
    return TestClient(app), store, runs


def _measured(store, job_id, diameter=33.4):
    store.update_job(
        job_id,
        status=JobStatus.MEASURED,
        engine="fake",
        mesh_path=paths.mesh_key(job_id),
        gcode_path=paths.gcode_key(job_id),
        keyframes_prefix=paths.keyframes_prefix(job_id),
    )
    store.put_object(paths.gcode_key(job_id), b"G21\nM30\n", "text/plain")
    for i in range(12):
        store.put_object(paths.keyframe_key(job_id, i), b"jpg", "image/jpeg")
    store.patch_result(
        job_id,
        {
            "diameter_mm": diameter,
            "tolerance_mm": 1.0,
            "within_tolerance": None,
            "outline_mm": [[0, 0]],
            "wafer_outline_mm": [[0, 0]],
            "orientation_method": "aruco+ransac",
            "gcode": "should never leak",
            "timings_s": {"extract": 30.0, "reconstruct": 900.0, "measure": 9.0},
            "diagnostics": {"diameter_profile": [[1.0, 39.1], [2.0, 33.0]]},
        },
    )


def test_admin_upload_stamps_truth_and_model():
    client, store, _ = _client()
    r = client.post(
        "/admin/scans",
        files={"video": ("m1.mov", b"video-bytes", "video/quicktime")},
        data={"model_name": "Model A", "truth_mm": "33.2", "notes": "take 1"},
    )
    assert r.status_code == 201
    job = store.get_job(r.json()["id"])
    assert job.config["model_name"] == "Model A" and job.config["truth_mm"] == 33.2
    assert job.config["reference_point"] == "base at skin junction"
    assert job.config["source"] == "admin" and job.config["marker_side_mm"]
    assert job.result["timings_s"]["upload"] >= 0


def test_list_and_detail_expose_everything_the_patient_api_hides():
    client, store, runs = _client()
    job_id = client.post(
        "/admin/scans",
        files={"video": ("m1.mov", b"v", "video/quicktime")},
        data={"model_name": "A", "truth_mm": "33.0"},
    ).json()["id"]
    _measured(store, job_id, 33.4)
    store.update_job(job_id, error_detail="raw", worker_id="w1")

    rows = client.get("/admin/scans").json()["jobs"]
    assert rows[0]["id"] == job_id and rows[0]["diameter_mm"] == 33.4
    assert rows[0]["deviation_mm"] == 0.4 and rows[0]["within_tolerance"] is True
    assert rows[0]["total_s"] == 939.0

    d = client.get(f"/admin/scans/{job_id}").json()
    assert d["error_detail"] == "raw" and d["worker_id"] == "w1"
    assert d["timings"]["bottleneck"] == "reconstruct" and d["timings"]["within_budget"] is False
    assert d["result"]["diagnostics"]["diameter_profile"][1] == [2.0, 33.0]
    assert "gcode" not in d["result"]
    assert d["artifacts"]["gcode_url"] and len(d["artifacts"]["keyframe_urls"]) == 6
    assert d["config"]["truth_mm"] == 33.0

    # the patient endpoint still hides internals
    p = client.get(f"/scans/{job_id}").json()
    assert "error_detail" not in p and "worker_id" not in p


def test_patch_truth_recomputes_and_syncs_run_log():
    client, store, runs = _client()
    job_id = client.post(
        "/admin/scans", files={"video": ("m1.mov", b"v", "video/quicktime")}
    ).json()["id"]
    _measured(store, job_id, 33.4)
    assert client.get(f"/admin/scans/{job_id}").json()["within_tolerance"] is None

    d = client.patch(
        f"/admin/scans/{job_id}",
        json={"truth_mm": 34.8, "model_name": "Model A", "reference_point": "widest"},
    ).json()
    assert d["deviation_mm"] == -1.4 and d["within_tolerance"] is False
    assert d["run"]["truth_mm"] == 34.8 and d["run"]["passed"] is False
    assert d["run"]["model_name"] == "Model A" and d["run"]["reference_point"] == "widest"
    # the patient poll now carries the recomputed values too
    assert client.get(f"/scans/{job_id}").json()["result"]["within_tolerance"] is False

    # correcting the truth updates the same run row, never a second one
    d2 = client.patch(f"/admin/scans/{job_id}", json={"truth_mm": 33.0}).json()
    assert d2["within_tolerance"] is True and len(runs.list()) == 1
    assert runs.list()[0].id == d["run"]["id"]

    # clearing truth clears deviation; empty strings clear text fields
    d3 = client.patch(f"/admin/scans/{job_id}", json={"truth_mm": None, "notes": ""}).json()
    assert d3["deviation_mm"] is None and d3["within_tolerance"] is None
    assert d3["notes"] is None


def test_gcode_and_csv_downloads():
    client, store, runs = _client()
    job_id = client.post(
        "/admin/scans", files={"video": ("m1.mov", b"v", "video/quicktime")}
    ).json()["id"]
    assert client.get(f"/admin/scans/{job_id}/gcode").status_code == 404
    _measured(store, job_id)
    assert client.get(f"/admin/scans/{job_id}/gcode").text == "G21\nM30\n"
    client.patch(f"/admin/scans/{job_id}", json={"truth_mm": 33.0, "model_name": "A"})
    csv = client.get("/admin/report.csv")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert "A" in csv.text and "33.4" in csv.text


def test_admin_404s():
    client, _, _ = _client()
    assert client.get("/admin/scans/nope").status_code == 404
    assert client.patch("/admin/scans/nope", json={"truth_mm": 1}).status_code == 404
