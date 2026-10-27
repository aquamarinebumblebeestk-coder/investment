import pytest

from jpstock.analyzer import PriceBar
from jpstock.technical import (
    BEARISH,
    BULLISH,
    analyze_technicals,
    bollinger,
    ema,
    last_cross,
    macd,
    rsi,
    sma,
)


def _bars(closes, volumes=None):
    volumes = volumes or [1000] * len(closes)
    return [PriceBar(f"d{i:03d}", c, c + 1, c - 1, c, v) for i, (c, v) in enumerate(zip(closes, volumes))]


def test_sma_and_ema():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    e = ema([1, 2, 3, 4], 2)  # k=2/3, 初期値=1.5
    assert e[:2] == [None, 1.5]
    assert e[2] == pytest.approx(3 * 2 / 3 + 1.5 / 3)


def test_rsi_extremes():
    assert rsi(list(range(1, 31)))[-1] == 100.0
    assert rsi(list(range(30, 0, -1)))[-1] == 0.0
    assert rsi([10.0] * 30)[-1] == 50.0
    assert rsi([1, 2, 3])[-1] is None


def test_rsi_alternating_is_balanced():
    values = [10 + (i % 2) for i in range(40)]
    assert rsi(values)[-1] == pytest.approx(50, abs=5)


def test_bollinger_constant_series():
    upper, mid, lower = bollinger([5.0] * 25)
    assert upper[-1] == mid[-1] == lower[-1] == 5.0
    assert upper[18] is None


def test_macd_linear_trend_positive():
    line, signal, hist = macd([float(i) for i in range(100)])
    assert line[-1] > 0
    assert signal[-1] == pytest.approx(line[-1])
    assert line[24] is None and line[25] is not None
    assert signal[32] is None and signal[33] is not None  # 26本目から MACD、その9本目からシグナル


def test_last_cross():
    assert last_cross([1, 3], [2, 2], 5) == ("golden", 0)
    assert last_cross([3, 1, 1], [2, 2, 2], 5) == ("dead", 1)
    assert last_cross([1, 1], [2, 2], 5) is None


def test_uptrend_is_bullish():
    ta = analyze_technicals(_bars([100 + i * 0.5 + (i % 3) for i in range(150)]))
    assert ta.sma[75] is not None
    trend = next(s for s in ta.signals if s.name == "トレンド")
    assert trend.stance == BULLISH
    assert ta.overall != BEARISH


def test_downtrend_is_bearish():
    ta = analyze_technicals(_bars([200 - i * 0.5 - (i % 3) for i in range(150)]))
    assert next(s for s in ta.signals if s.name == "トレンド").stance == BEARISH
    assert ta.overall == BEARISH


def test_golden_cross_and_volume_surge():
    closes = [100 - i for i in range(40)] + [61 + i * 3 for i in range(8)]
    volumes = [1000] * (len(closes) - 1) + [5000]
    ta = analyze_technicals(_bars(closes, volumes))
    names = {(s.name, s.stance) for s in ta.signals}
    assert ("移動平均クロス", BULLISH) in names
    assert ("出来高", BULLISH) in names
    assert ta.volume_ratio == pytest.approx(5.0)


def test_short_history_does_not_crash():
    ta = analyze_technicals(_bars([100, 101, 102]))
    assert ta.rsi is None and ta.sma[75] is None
    assert analyze_technicals([]).close is None
