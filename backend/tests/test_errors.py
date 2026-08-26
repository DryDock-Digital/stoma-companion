from __future__ import annotations

from app.errors import DEFAULT_MESSAGES, StageError, StageTimeout, failure_fields


def test_stage_error_defaults_and_override():
    e = StageError("raw", stage="marker")
    assert e.user_message == DEFAULT_MESSAGES["marker"]
    e2 = StageError("raw", stage="marker", user_message="Custom.")
    assert e2.user_message == "Custom."
    assert StageTimeout("slow").stage == "timeout"


def test_failure_fields_never_leak_raw_text():
    f = failure_fields(RuntimeError("/opt/colmap/bin: segfault at 0xdead"), "reconstruct")
    assert f["error"] == DEFAULT_MESSAGES["reconstruct"]
    assert "segfault" in f["error_detail"] and f["error_stage"] == "reconstruct"
    assert "segfault" not in f["error"]


def test_messages_are_plain_language():
    banned = ("colmap", "mesh", "3d", "reconstruct", "aruco", "marker", "stderr", "exception")
    for text in DEFAULT_MESSAGES.values():
        low = text.lower()
        assert not any(b in low for b in banned), text
        assert text.endswith(".")
