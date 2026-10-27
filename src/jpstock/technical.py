"""テクニカル分析: 日足の終値・出来高から代表的な指標とシグナルを計算する。

API 呼び出しは行わず、analyzer が整形した株価 (調整後) だけを入力にする。
シグナルは機械的な判定であり、売買推奨ではない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analyzer import PriceBar

SMA_PERIODS = (5, 25, 75)
RSI_PERIOD = 14
RSI_OVERBOUGHT, RSI_OVERSOLD = 70.0, 30.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_SIGMA = 20, 2.0
VOLUME_PERIOD = 20
VOLUME_SURGE = 2.0  # 平均出来高の何倍で「急増」とみなすか
CROSS_LOOKBACK = 5  # 直近何営業日以内のクロスを報告するか

BULLISH, BEARISH, NEUTRAL = "強気", "弱気", "中立"

Series = list[float | None]


# ---- 指標の計算 (系列の長さは入力と同じ。計算できない先頭部分は None) ----------
def sma(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> Series:
    """最初の period 本の単純平均を起点にした指数平滑移動平均。"""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = RSI_PERIOD) -> Series:
    """Wilder の平滑化による RSI。"""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def calc(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)

    out[period] = calc(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = calc(avg_gain, avg_loss)
    return out


def macd(values: list[float]) -> tuple[Series, Series, Series]:
    """(MACD, シグナル, ヒストグラム)"""
    fast, slow = ema(values, MACD_FAST), ema(values, MACD_SLOW)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    signal_part = ema([v for v in line[start:] if v is not None], MACD_SIGNAL)
    signal: Series = [None] * start + signal_part
    hist: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, signal)]
    return line, signal, hist


def bollinger(values: list[float], period: int = BB_PERIOD, k: float = BB_SIGMA) -> tuple[Series, Series, Series]:
    """(上限, 中心, 下限)。標準偏差は母標準偏差。"""
    mid = sma(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i, m in enumerate(mid):
        if m is None:
            continue
        window = values[i - period + 1 : i + 1]
        sd = (sum((v - m) ** 2 for v in window) / period) ** 0.5
        upper[i], lower[i] = m + k * sd, m - k * sd
    return upper, mid, lower


def last_cross(fast: Series, slow: Series, lookback: int) -> tuple[str, int] | None:
    """直近 lookback 本以内のクロス ("golden"/"dead", 何本前か)。"""
    n = len(fast)
    for back in range(lookback):
        i = n - 1 - back
        if i < 1:
            break
        a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if None in (a0, b0, a1, b1):
            break
        if a0 <= b0 and a1 > b1:
            return "golden", back
        if a0 >= b0 and a1 < b1:
            return "dead", back
    return None


# ---- 分析結果 ---------------------------------------------------------------
@dataclass
class Signal:
    name: str
    stance: str  # BULLISH / BEARISH / NEUTRAL
    detail: str


@dataclass
class TechnicalAnalysis:
    as_of: str | None = None
    close: float | None = None
    sma: dict[int, float | None] = field(default_factory=dict)
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    volume_ratio: float | None = None  # 直近出来高 / 20日平均
    high: float | None = None  # 取得期間の高値
    low: float | None = None
    signals: list[Signal] = field(default_factory=list)

    @property
    def score(self) -> int:
        """強気シグナル数 - 弱気シグナル数 (総合判定の目安)。"""
        return sum(s.stance == BULLISH for s in self.signals) - sum(s.stance == BEARISH for s in self.signals)

    @property
    def overall(self) -> str:
        return BULLISH if self.score >= 2 else BEARISH if self.score <= -2 else NEUTRAL


def _last(series: Series) -> float | None:
    return series[-1] if series else None


def analyze_technicals(prices: list[PriceBar]) -> TechnicalAnalysis:
    bars = [b for b in prices if b.close is not None]
    ta = TechnicalAnalysis()
    if not bars:
        return ta
    closes = [b.close for b in bars]  # type: ignore[misc]
    close = closes[-1]
    ta.as_of, ta.close = bars[-1].date, close
    highs = [b.high for b in bars if b.high is not None]
    lows = [b.low for b in bars if b.low is not None]
    ta.high = max(highs) if highs else None
    ta.low = min(lows) if lows else None

    smas = {p: sma(closes, p) for p in SMA_PERIODS}
    ta.sma = {p: _last(s) for p, s in smas.items()}
    ta.rsi = _last(rsi(closes))
    m_line, m_signal, _ = macd(closes)
    ta.macd, ta.macd_signal = _last(m_line), _last(m_signal)
    upper, _, lower = bollinger(closes)
    ta.bb_upper, ta.bb_lower = _last(upper), _last(lower)
    vols = [b.volume for b in bars if b.volume is not None]
    if len(vols) > VOLUME_PERIOD:
        avg = sum(vols[-VOLUME_PERIOD - 1 : -1]) / VOLUME_PERIOD  # 当日を除く20日平均
        ta.volume_ratio = vols[-1] / avg if avg else None

    sig = ta.signals
    # トレンド (移動平均の並び)
    s5, s25, s75 = (ta.sma.get(p) for p in SMA_PERIODS)
    if s25 is not None and s75 is not None:
        if close > s25 > s75:
            sig.append(Signal("トレンド", BULLISH, "株価 > 25日線 > 75日線 (上昇トレンド)"))
        elif close < s25 < s75:
            sig.append(Signal("トレンド", BEARISH, "株価 < 25日線 < 75日線 (下降トレンド)"))
        else:
            sig.append(Signal("トレンド", NEUTRAL, "移動平均の並びが混在 (もみ合い)"))
    elif s25 is not None:
        stance = BULLISH if close > s25 else BEARISH
        sig.append(Signal("トレンド", stance, f"株価は25日線の{'上' if close > s25 else '下'}"))

    # ゴールデンクロス / デッドクロス
    for fast_p, slow_p in ((5, 25), (25, 75)):
        cross = last_cross(smas[fast_p], smas[slow_p], CROSS_LOOKBACK)
        if cross:
            kind, back = cross
            when = "本日" if back == 0 else f"{back}日前"
            if kind == "golden":
                sig.append(Signal("移動平均クロス", BULLISH, f"{fast_p}日線と{slow_p}日線のゴールデンクロス ({when})"))
            else:
                sig.append(Signal("移動平均クロス", BEARISH, f"{fast_p}日線と{slow_p}日線のデッドクロス ({when})"))

    # RSI
    if ta.rsi is not None:
        if ta.rsi >= RSI_OVERBOUGHT:
            sig.append(Signal("RSI", BEARISH, f"RSI {ta.rsi:.1f} (買われすぎ)"))
        elif ta.rsi <= RSI_OVERSOLD:
            sig.append(Signal("RSI", BULLISH, f"RSI {ta.rsi:.1f} (売られすぎ)"))
        else:
            sig.append(Signal("RSI", NEUTRAL, f"RSI {ta.rsi:.1f}"))

    # MACD
    if ta.macd is not None and ta.macd_signal is not None:
        cross = last_cross(m_line, m_signal, CROSS_LOOKBACK)
        if cross:
            kind, back = cross
            when = "本日" if back == 0 else f"{back}日前"
            stance = BULLISH if kind == "golden" else BEARISH
            word = "上抜け" if kind == "golden" else "下抜け"
            sig.append(Signal("MACD", stance, f"MACDがシグナルを{word} ({when})"))
        else:
            stance = BULLISH if ta.macd > ta.macd_signal else BEARISH
            sig.append(Signal("MACD", stance, f"MACD {'>' if stance == BULLISH else '<'} シグナル"))

    # ボリンジャーバンド
    if ta.bb_upper is not None and ta.bb_lower is not None:
        if close >= ta.bb_upper:
            sig.append(Signal("ボリンジャー", BEARISH, f"+{BB_SIGMA:.0f}σ 以上 (過熱気味)"))
        elif close <= ta.bb_lower:
            sig.append(Signal("ボリンジャー", BULLISH, f"-{BB_SIGMA:.0f}σ 以下 (売られすぎ気味)"))

    # 出来高
    if ta.volume_ratio is not None and ta.volume_ratio >= VOLUME_SURGE:
        prev_close = closes[-2] if len(closes) > 1 else close
        stance = BULLISH if close >= prev_close else BEARISH
        sig.append(Signal("出来高", stance, f"出来高が20日平均の {ta.volume_ratio:.1f} 倍に急増"))
    return ta
