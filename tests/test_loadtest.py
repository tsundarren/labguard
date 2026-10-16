from scripts.loadtest import percentile


def test_nearest_rank_percentiles():
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100


def test_edge_cases():
    assert percentile([], 50) is None
    assert percentile([7.0], 99) == 7.0
    assert percentile([1.0, 2.0], 1) == 1.0
