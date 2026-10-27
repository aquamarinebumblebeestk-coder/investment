"""API を叩かずに Analyzer を動かすためのフェイク。V2 のフィールド名に合わせたサンプルデータ。"""

from jpstock.client import PlanNotSupportedError

MASTER = [{"Date": "2026-07-31", "Code": "72030", "CoName": "トヨタ自動車", "MktNm": "プライム", "S33Nm": "輸送用機器"}]

BARS = [
    {"Date": f"2026-07-{d:02d}", "Code": "72030", "AdjO": 2800 + d, "AdjH": 2850 + d, "AdjL": 2790 + d,
     "AdjC": 2820 + d, "AdjVo": 1_000_000 + d}
    for d in range(1, 31)
]


def _fy(fy_end, disc, sales, op, np_, eps, bps, div, nxt=None):
    row = {"DiscDate": disc, "DiscTime": "15:00", "Code": "72030", "CurPerType": "FY", "CurFYEn": fy_end,
           "NxtFYEn": nxt or "", "Sales": str(sales), "OP": str(op), "NP": str(np_), "EPS": str(eps),
           "BPS": str(bps), "Eq": str(np_ * 10), "DivAnn": str(div), "PayoutRatioAnn": "0.3",
           "ShOutFY": "1000", "TrShFY": "0",
           "TA": str(np_ * 25), "EqAR": "0.4", "CFO": str(np_ * 1.2), "CFI": str(-np_ * 0.5),
           "CFF": str(-np_ * 0.3), "CashEq": str(np_)}
    return row


SUMMARY = [
    _fy("2025-03-31", "2025-05-08", 40e12, 4e12, 3e12, 200.0, 2000.0, 60),
    # 四半期決算 (除外される)
    {"DiscDate": "2025-08-01", "CurPerType": "1Q", "CurFYEn": "2026-03-31", "Sales": "10e12",
     "FSales": "41e12", "FEPS": "210"},
    _fy("2026-03-31", "2026-05-08", 44e12, 4.4e12, 3.3e12, 220.0, 2200.0, 70, nxt="2027-03-31")
    | {"NxFSales": "46e12", "NxFOP": "4.6e12", "NxFNp": "3.4e12", "NxFEPS": "230", "NxFDivAnn": "75"},
    # 予想修正のみの開示 (実績なし) → 実績からは除外される
    {"DiscDate": "2026-05-09", "CurPerType": "FY", "CurFYEn": "2026-03-31", "Sales": ""},
]


class FakeClient:
    def __init__(self, plan, valuation_error=False):
        self.plan = plan
        self.valuation_error = valuation_error
        self.calls = []

    def equities_master(self, code):
        self.calls.append("master")
        return MASTER

    def daily_bars(self, code, start, end):
        self.calls.append(("bars", start, end))
        return BARS

    def fin_summary(self, code):
        self.calls.append("summary")
        return SUMMARY

    def valuation(self, code, start, end):
        self.calls.append("valuation")
        if self.valuation_error:
            raise PlanNotSupportedError("no", 403)
        return [{"Date": "2026-07-30", "EPS": "220", "FwdEPS": "230", "BPS": "2200", "PER": "13",
                 "FwdPER": "12.4", "PBR": "1.3", "ROE": "0.1", "MktCap": "2860000"}]

    def dividends(self, code):
        self.calls.append("dividends")
        return [{"RecDate": "2026-03-31", "ExDate": "2026-03-30", "PayDate": "2026-05-25", "DivRate": "35",
                 "IFCode": "2"}]
