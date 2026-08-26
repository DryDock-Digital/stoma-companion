"""Stage-tagged errors with patient-safe messages.

Raw exception text (COLMAP stderr, Supabase messages, file paths) must never reach
the patient's screen (FR-13 / NFR-05). Every pipeline stage records two things on a
failed job: `error` — a plain-language sentence the app shows as-is — and
`error_detail` — the raw text, server-side only. `StageError` carries both; anything
else is mapped through `user_message_for`.
"""

from __future__ import annotations

# One sentence per stage, written for a 60+ patient. No jargon, always an action.
DEFAULT_MESSAGES: dict[str, str] = {
    "extract": "We couldn't read that video. Please record it again.",
    "reconstruct": (
        "We couldn't build a clear picture from that video. "
        "Please record again, moving the phone slowly all the way around."
    ),
    "measure": "We couldn't take the measurement from that video. Please try again.",
    "marker": (
        "We couldn't see the square card clearly. Make sure it lies flat next to the "
        "stoma, stays in view, and try again in good light."
    ),
    "cut": "The cutter didn't finish. Please ask for help.",
    "timeout": "This took longer than expected. Please try again.",
    "unknown": "Something went wrong. Please try again.",
}


class StageError(Exception):
    """An error a pipeline stage can raise with an explicit patient-safe message.
    `stage` is *where* it happened; `message_key` picks the sentence (defaults to
    the stage, but e.g. a timeout says "took longer than expected" wherever it
    happened)."""

    stage: str = "unknown"
    message_key: str | None = None

    def __init__(self, detail: str, *, stage: str | None = None, user_message: str | None = None):
        super().__init__(detail)
        self.detail = detail
        if stage is not None:
            self.stage = stage
        key = self.message_key or self.stage
        self.user_message = user_message or DEFAULT_MESSAGES.get(key, DEFAULT_MESSAGES["unknown"])


class StageTimeout(StageError):
    stage = "timeout"
    message_key = "timeout"


def user_message_for(exc: BaseException, stage: str) -> str:
    """Patient-safe sentence for any exception raised in `stage`."""
    if isinstance(exc, StageError):
        return exc.user_message
    return DEFAULT_MESSAGES.get(stage, DEFAULT_MESSAGES["unknown"])


def failure_fields(exc: BaseException, stage: str) -> dict:
    """The job-row update for a failure: patient-safe `error`, raw `error_detail`,
    and the `error_stage`."""
    stage_name = exc.stage if isinstance(exc, StageError) else stage
    detail = f"{type(exc).__name__}: {exc}"
    return {
        "error": user_message_for(exc, stage_name),
        "error_detail": detail[:4000],
        "error_stage": stage_name,
    }
