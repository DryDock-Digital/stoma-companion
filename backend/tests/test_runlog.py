"""P5-1 run log — record derivation + in-memory store."""

from __future__ import annotations

import pytest

from app.runlog import InMemoryRunStore, RunRecord


def test_build_derives_deviation_and_pass():
    r = RunRecord.build(model_name="m1", measured_mm=33.4, truth_mm=33.0, tolerance_mm=1.0)
    assert r.deviation_mm == pytest.approx(0.4)
    assert r.abs_deviation_mm == pytest.approx(0.4)
    assert r.passed is True

    fail = RunRecord.build(model_name="m2", measured_mm=35.5, truth_mm=33.0)
    assert fail.abs_deviation_mm == pytest.approx(2.5)
    assert fail.passed is False


def test_build_without_truth_leaves_pass_unknown():
    r = RunRecord.build(model_name="m", measured_mm=30.0)
    assert r.truth_mm is None
    assert r.deviation_mm is None
    assert r.passed is None


def test_store_insert_list_delete():
    store = InMemoryRunStore()
    a = store.insert(RunRecord.build(model_name="a", measured_mm=33.0, truth_mm=33.0))
    store.insert(RunRecord.build(model_name="b", measured_mm=34.0, truth_mm=33.0))
    assert a.id and a.created_at

    all_runs = store.list()
    assert len(all_runs) == 2
    assert {r.model_name for r in all_runs} == {"a", "b"}
    assert len(store.list(model_name="a")) == 1

    store.delete(a.id)
    assert len(store.list()) == 1
