from __future__ import annotations

from backend.utils.db_batching import MAX_BIND_PARAMS, bind_batch_size, bind_batches


def test_bind_batch_size_stays_within_the_budget():
    assert bind_batch_size(25) * 25 <= MAX_BIND_PARAMS
    assert bind_batch_size(1) == MAX_BIND_PARAMS


def test_bind_batch_size_never_returns_zero():
    # A zero-length batch would make bind_batches loop without consuming
    # anything, so an item wider than the whole budget is attempted alone.
    assert bind_batch_size(MAX_BIND_PARAMS * 2) == 1
    assert bind_batch_size(0) == MAX_BIND_PARAMS
    assert bind_batch_size(-1) == MAX_BIND_PARAMS


def test_bind_batches_covers_every_item_in_order():
    items = list(range(MAX_BIND_PARAMS * 2 + 7))

    batches = list(bind_batches(items))

    assert [item for batch in batches for item in batch] == items
    assert len(batches) == 3


def test_bind_batches_respects_the_parameter_width():
    items = list(range(5_000))

    batches = list(bind_batches(items, params_per_item=25))

    assert all(len(batch) * 25 <= MAX_BIND_PARAMS for batch in batches)
    assert [item for batch in batches for item in batch] == items


def test_bind_batches_yields_nothing_for_an_empty_sequence():
    assert list(bind_batches([])) == []


def test_bind_batches_yields_one_batch_when_everything_fits():
    items = list(range(10))

    assert [list(batch) for batch in bind_batches(items)] == [items]
