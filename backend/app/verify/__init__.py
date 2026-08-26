"""Verification harness (P2-1).

The scoring framework the P2 algorithm work reports against: given each fixture's
mesh + caliper truth, run a measurement *method*, compute the deviation vs the
±1 mm tolerance (FR-09), and roll it up into a one-command scoreboard.

Every later P2 ticket (orientation via ArUco plane, fallbacks, auto slice height)
is just a new `MeasurementMethod` scored by this same harness, so improvements are
comparable on one board. This grows into the P5 verification/test-log module —
`RunResult` rows are already CSV-serialisable for that.

    from app.verify import run_scoreboard, METHODS, discover_fixtures
"""

from .fixtures import Fixture, discover_fixtures, load_mesh
from .harness import (
    METHODS,
    BaselineDiameterMethod,
    MeasuredResult,
    MeasurementMethod,
    RunResult,
    Scoreboard,
    run_scoreboard,
)

__all__ = [
    "Fixture",
    "discover_fixtures",
    "load_mesh",
    "METHODS",
    "BaselineDiameterMethod",
    "MeasuredResult",
    "MeasurementMethod",
    "RunResult",
    "Scoreboard",
    "run_scoreboard",
]
