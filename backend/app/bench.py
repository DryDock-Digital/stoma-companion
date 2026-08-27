"""Batch benchmark runner — `stoma-bench` (P1-5 test campaign).

Drives a folder of test videos through the live system via the admin API and
turns the runs into a benchmark sheet:

    stoma-bench run    --manifest m.csv --root /videos --api https://… --model "Model 1" \\
                       --truth 32.8 --truth-min 31.2 [--state bench-state.json] [--no-wait]
    stoma-bench report --state bench-state.json --api https://… --out results/

Manifest CSV columns: video (path relative to --root), and any number of attribute
columns (group, distance_mm, angle_deg, light, phone, take, res, legacy_mm, …) —
every non-empty attribute becomes a tag on the job, so the bench and the report can
group by it. Re-runnable: videos already in the state file are skipped unless
--force. The report writes results.csv (one row per run), summary.csv (per tag
value: runs, passes, pass rate, mean/max |Δ| widest and narrowest, repeatability
across takes, mean seconds) and summary.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

TERMINAL = {"measured", "done", "failed"}
SUMMARY_KEYS = ("group", "distance_mm", "angle_deg", "light", "phone", "res")


def _load_manifest(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "video" not in rows[0]:
        sys.exit("manifest needs a 'video' column")
    return rows


def _tags_for(row: dict) -> dict:
    skip = {"video", "model", "truth_widest_mm", "truth_narrowest_mm"}
    out = {}
    for k, v in row.items():
        if k in skip or v is None or str(v).strip() == "":
            continue
        v = str(v).strip()
        try:
            out[k] = int(v) if v.lstrip("-").isdigit() else float(v) if _is_float(v) else v
        except ValueError:
            out[k] = v
    return out


def _is_float(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _load_state(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"jobs": {}}


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=1))


def cmd_run(a) -> int:
    api = a.api.rstrip("/")
    rows = _load_manifest(Path(a.manifest))
    state_path = Path(a.state)
    state = _load_state(state_path)
    client = httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0))
    for row in rows:
        video = row["video"]
        if video in state["jobs"] and not a.force:
            continue
        path = Path(a.root) / video
        if not path.exists():
            print(f"skip (missing): {video}")
            continue
        model = row.get("model") or a.model
        truth = row.get("truth_widest_mm") or a.truth
        truth_min = row.get("truth_narrowest_mm") or a.truth_min
        tags = _tags_for(row)
        data = {"model_name": model, "notes": f"bench {video}", "tags": json.dumps(tags)}
        if truth not in (None, ""):
            data["truth_mm"] = str(truth)
        if truth_min not in (None, ""):
            data["truth_min_mm"] = str(truth_min)
        ctype = "video/quicktime" if path.suffix.lower() == ".mov" else "video/mp4"
        for _attempt in range(3):
            try:
                with open(path, "rb") as fh:
                    r = client.post(
                        f"{api}/admin/scans", data=data, files={"video": (path.name, fh, ctype)}
                    )
                if r.status_code == 201:
                    break
                print(f"  upload {video}: HTTP {r.status_code} {r.text[:120]}")
            except httpx.HTTPError as exc:
                print(f"  upload {video}: {exc}")
            time.sleep(5)
        else:
            state["jobs"][video] = {"error": "upload failed"}
            _save_state(state_path, state)
            continue
        job_id = r.json()["id"]
        state["jobs"][video] = {"id": job_id, "tags": tags}
        _save_state(state_path, state)
        print(f"queued {video} → {job_id[:8]}")
    if a.no_wait:
        return 0
    return _wait(client, api, state, state_path)


def _wait(client, api, state, state_path) -> int:
    pending = {v: j["id"] for v, j in state["jobs"].items() if "id" in j and not j.get("final")}
    print(f"waiting on {len(pending)} run(s) …")
    while pending:
        for video, jid in list(pending.items()):
            try:
                d = client.get(f"{api}/admin/scans/{jid}").json()
            except Exception:  # noqa: BLE001
                continue
            if d.get("status") in TERMINAL:
                state["jobs"][video]["final"] = d["status"]
                _save_state(state_path, state)
                print(
                    f"  {video}: {d['status']} Ø {d.get('diameter_mm')}/{d.get('min_width_mm')} "
                    f"dev {d.get('deviation_mm')}/{d.get('deviation_min_mm')} "
                    f"{'PASS' if d.get('within_tolerance') else 'FAIL'} {d['total_s']:.0f}s"
                )
                del pending[video]
        if pending:
            time.sleep(15)
    return 0


def cmd_report(a) -> int:
    api = a.api.rstrip("/")
    state = _load_state(Path(a.state))
    client = httpx.Client(timeout=60.0)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for video, j in state["jobs"].items():
        if "id" not in j:
            rows.append({"video": video, "status": "upload-failed"})
            continue
        d = client.get(f"{api}/admin/scans/{j['id']}").json()
        r = d.get("result") or {}
        g = r.get("diagnostics") or {}
        rec = d.get("result", {}).get("reconstruction") or {}
        st = rec.get("step_timings_s") or {}
        rows.append(
            {
                "video": video,
                "job": j["id"],
                "status": d.get("status"),
                "model": d.get("model_name"),
                **{f"tag_{k}": v for k, v in (d.get("tags") or {}).items()},
                "frames": d.get("keyframe_count"),
                "total_s": d.get("total_s"),
                "reconstruct_s": (r.get("timings_s") or {}).get("reconstruct"),
                "mapper_s": st.get("mapper"),
                "mesh_s": st.get("mesh"),
                "densify_s": st.get("densify"),
                "widest_mm": d.get("diameter_mm"),
                "narrowest_mm": d.get("min_width_mm"),
                "truth_widest_mm": d.get("truth_mm"),
                "truth_narrowest_mm": d.get("truth_min_mm"),
                "dev_widest_mm": d.get("deviation_mm"),
                "dev_narrowest_mm": d.get("deviation_min_mm"),
                "pass": d.get("within_tolerance"),
                "marker_views": r.get("marker_views"),
                "reproj_px": g.get("marker_reprojection_px"),
                "scale_cv": g.get("marker_side_cv"),
                "outline_method": g.get("outline_method"),
                "slice_h_mm": g.get("slice_height_mm_above_skin"),
                "error": d.get("error"),
                "error_stage": d.get("error_stage"),
            }
        )
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k.startswith("tag_"), k))
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # per-attribute summaries
    lines = ["# Benchmark summary", ""]
    summary_rows = []
    measured = [r for r in rows if r.get("status") == "measured"]
    lines.append(
        f"{len(rows)} videos · {len(measured)} measured · "
        f"{sum(1 for r in measured if r.get('pass'))} pass · "
        f"{sum(1 for r in rows if r.get('status') == 'failed')} failed"
    )
    for key in SUMMARY_KEYS:
        col = f"tag_{key}"
        values = sorted({str(r[col]) for r in rows if r.get(col) not in (None, "")})
        if not values:
            continue
        lines += [
            "",
            f"## by {key}",
            "",
            "| value | runs | measured | pass | pass % | mean|Δ| widest | max|Δ| widest | "
            "mean|Δ| narrowest | max|Δ| narrowest | spread widest | mean s |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for val in values:
            grp = [r for r in rows if str(r.get(col)) == val]
            m = [r for r in grp if r.get("status") == "measured"]
            dw = [abs(r["dev_widest_mm"]) for r in m if r.get("dev_widest_mm") is not None]
            dn = [abs(r["dev_narrowest_mm"]) for r in m if r.get("dev_narrowest_mm") is not None]
            widths = [r["widest_mm"] for r in m if r.get("widest_mm") is not None]
            passes = sum(1 for r in m if r.get("pass"))
            secs = [r["total_s"] for r in m if r.get("total_s")]
            row = {
                "key": key,
                "value": val,
                "runs": len(grp),
                "measured": len(m),
                "pass": passes,
                "pass_pct": round(100 * passes / len(m), 0) if m else None,
                "mean_abs_dev_widest": round(statistics.mean(dw), 2) if dw else None,
                "max_abs_dev_widest": round(max(dw), 2) if dw else None,
                "mean_abs_dev_narrowest": round(statistics.mean(dn), 2) if dn else None,
                "max_abs_dev_narrowest": round(max(dn), 2) if dn else None,
                "spread_widest": round(max(widths) - min(widths), 2) if len(widths) > 1 else None,
                "mean_total_s": round(statistics.mean(secs), 0) if secs else None,
            }
            summary_rows.append(row)
            cells = [
                val,
                row["runs"],
                row["measured"],
                row["pass"],
                row["pass_pct"],
                row["mean_abs_dev_widest"],
                row["max_abs_dev_widest"],
                row["mean_abs_dev_narrowest"],
                row["max_abs_dev_narrowest"],
                row["spread_widest"],
                row["mean_total_s"],
            ]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    with open(out / "summary.csv", "w", newline="") as fh:
        if summary_rows:
            w = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out / 'results.csv'}, {out / 'summary.csv'}, {out / 'summary.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stoma-bench", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="upload every manifest video and wait for results")
    r.add_argument("--manifest", required=True)
    r.add_argument(
        "--root", required=True, help="folder the manifest's video paths are relative to"
    )
    r.add_argument("--api", required=True)
    r.add_argument("--model", default=None)
    r.add_argument("--truth", default=None, help="widest caliper mm (manifest column overrides)")
    r.add_argument("--truth-min", default=None, help="narrowest caliper mm")
    r.add_argument("--state", default="bench-state.json")
    r.add_argument("--force", action="store_true", help="re-upload videos already in the state")
    r.add_argument("--no-wait", action="store_true")
    r.set_defaults(func=cmd_run)
    q = sub.add_parser("report", help="results.csv + per-attribute summary from the state file")
    q.add_argument("--state", default="bench-state.json")
    q.add_argument("--api", required=True)
    q.add_argument("--out", default="bench-results")
    q.set_defaults(func=cmd_report)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
