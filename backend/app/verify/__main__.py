"""One-command scoreboard (P2-1).

    stoma-score                     # score all fixtures with the baseline method
    python -m app.verify --help
    stoma-score --fixtures ../fixtures --tolerance 1.0 --csv out.csv

Exit code: 0 if all fixtures pass (or there are none yet), 1 if any fail/error —
so it can gate CI once fixtures land (P0-3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fixtures import discover_fixtures
from .harness import DEFAULT_TOLERANCE_MM, METHODS, run_scoreboard


def _default_fixtures_dir() -> Path:
    # backend/app/verify/__main__.py → repo root is parents[3]
    return Path(__file__).resolve().parents[3] / "fixtures"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stoma-score", description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=_default_fixtures_dir(),
        help="fixtures root (default: repo fixtures/)",
    )
    parser.add_argument("--method", choices=sorted(METHODS), default="baseline")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_MM,
        help="pass/fail tolerance in mm (FR-09 default 1.0)",
    )
    parser.add_argument("--csv", type=Path, help="also write per-run results as CSV")
    args = parser.parse_args(argv)

    fixtures = discover_fixtures(args.fixtures)
    board = run_scoreboard(fixtures, METHODS[args.method], tolerance_mm=args.tolerance)

    print(board.format_table())
    if args.csv:
        args.csv.write_text(board.to_csv())
        print(f"\nwrote {args.csv}")

    if not fixtures:
        return 0  # nothing to fail yet
    return 0 if board.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
