from datetime import date, timedelta

import pytest

from jpstock import screencli
from jpstock.screening import ScreeningError, format_value, parse_condition, screen
from jpstock.store import Store

START = date(2026, 1, 5)
DAYS = [(START + timedelta(days=i)).isoformat() for i in range(140) if (START + timedelta(days=i)).weekday() < 5]


def _fy(code, n, fy_end, disc, sales, op, np_, eps, bps, div):
    return {"Code": code, "DiscNo": f"{code}-{n}", "DiscDate": disc, "DiscTime": "15:00", "CurPerType": "FY",
            "CurFYEn": fy_end, "NxtFYEn": "", "Sales": str(sales), "OP": str(op), "NP": str(np_), "EPS": str(eps),
            "BPS": str(bps), "Eq": str(np_ * 8), "TA": str(np_ * 16), "EqAR": "0.5", "DivAnn": str(div),
            "CFO": str(np_ * 1.2), "CFI": str(-np_ * 0.3), "ShOutFY": "1000000", "TrShFY": "0"}


def _add_stock(s, code, name, *, price, trend, eps, bps, div, growth, margin, volume=100_000, sector="9", mkt="プライム",
               s33="3050"):
    c5 = code + "0"
    s.upsert_listings([{"Date": "2026-05-01", "Code": c5, "CoName": name, "MktNm": mkt, "S33Nm": sector, "S33": s33}])
    bars = []
    for i, d in enumerate(DAYS):
        p = price * (1 + trend * i / len(DAYS))
        bars.append({"Date": d, "Code": c5, "O": p, "H": p * 1.01, "L": p * 0.99, "C": p, "Vo": volume, "AdjFactor": 1})
    s.upsert_bars(bars)
    sales1 = 100e9
    sales2 = sales1 * (1 + growth)
    s.upsert_fins([
        _fy(c5, 1, "2025-03-31", "2025-05-10", sales1, sales1 * margin, sales1 * margin * 0.7, eps / (1 + growth), bps, div),
        _fy(c5, 2, "2026-03-31", "2026-05-10", sales2, sales2 * margin, sales2 * margin * 0.7, eps, bps, div),
    ])


@pytest.fixture
def store():
    with Store(":memory:") as s:
        _add_stock(s, "1111", "割安株", price=1000, trend=0.0, eps=125, bps=1250, div=40, growth=0.02, margin=0.08)
        _add_stock(s, "2222", "成長株", price=5000, trend=0.3, eps=100, bps=800, div=10, growth=0.25, margin=0.2)
        _add_stock(s, "3333", "低流動株", price=300, trend=0.0, eps=30, bps=400, div=3, growth=0.0, margin=0.05,
                   volume=100)
        _add_stock(s, "4444", "グロース株", price=800, trend=-0.3, eps=-10, bps=200, div=0, growth=0.4, margin=-0.05,
                   mkt="グロース")
        _add_stock(s, "1305", "ETF", price=2000, trend=0.0, eps=0, bps=0, div=0, growth=0, margin=0, s33="9999")
        s.commit()
        yield s


def test_parse_condition_units_and_errors():
    assert parse_condition("roe>=8%").value == pytest.approx(0.08)
    assert parse_condition("turnover >= 1億").value == 1e8
    assert parse_condition("ｐｅｒ＜＝１５").value == 15  # 全角も可
    assert parse_condition("market=プライム").value == "プライム"
    for bad in ("per", "foo<1", "per<abc"):
        with pytest.raises(ScreeningError):
            parse_condition(bad)


def test_screen_excludes_etf_and_sorts_by_score(store):
    res = screen(store, [])
    codes = [r["code"] for r in res.rows]
    assert "1305" not in codes and res.universe_size == 4
    assert res.analyzed == 4 and res.matched == 4
    scores = [r["score"] for r in res.rows if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_conditions_filter(store):
    res = screen(store, [parse_condition(c) for c in ("per>0", "per<=10", "yield>=3%")])
    assert [r["code"] for r in res.rows] == ["1111"]  # PER 8, 利回り 4%
    res = screen(store, [parse_condition("sales_cagr>=20%")], sort="sales_cagr")
    assert [r["code"] for r in res.rows] == ["4444", "2222"]
    res = screen(store, [parse_condition("turnover>=1000万")])
    assert "3333" not in [r["code"] for r in res.rows]


def test_market_filter_and_missing_values(store):
    res = screen(store, [], markets=["グロース"])
    assert [r["code"] for r in res.rows] == ["4444"]
    # 赤字で PER がない銘柄は PER 条件を満たさない
    res = screen(store, [parse_condition("per<100")])
    assert "4444" not in [r["code"] for r in res.rows]


def test_as_of_uses_only_past_data(store):
    res = screen(store, [], as_of="2026-03-31")
    row = next(r for r in res.rows if r["code"] == "2222")
    assert row["sales_cagr"] is None  # 2026-05 の決算はまだ見えない
    assert row["close"] < 5000 * 1.3


def test_format_value():
    assert format_value("roe", 0.123) == "12.3%"
    assert format_value("turnover", 1.5e8) == "1.5"
    assert format_value("per", None) == "-"


def test_cli(store, tmp_path, capsys, monkeypatch):
    db = tmp_path / "x.db"
    dst = Store(db)
    store.conn.backup(dst.conn)
    dst.close()
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")  # API を使わないのでリモートでも動く
    csv_path = tmp_path / "out.csv"
    assert screencli.main(["--db", str(db), "--preset", "value", "--csv", str(csv_path)]) == 0
    out = capsys.readouterr().out
    assert "割安株" in out and "該当 1 銘柄" in out
    assert csv_path.read_text(encoding="utf-8-sig").count("\n") == 2
    assert screencli.main(["--db", str(db), "foo>1"]) == 1
    assert screencli.main(["--db", str(tmp_path / "none.db")]) == 1
    assert screencli.main(["--list-metrics"]) == 0
