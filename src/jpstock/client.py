"""J-Quants API V2 クライアント (必要なエンドポイントのみの薄いラッパー)。

認証: ダッシュボードで発行した API キーを `x-api-key` ヘッダに付与する。
レート制限: プランごとの「リクエスト/分」に合わせてクライアント側で間隔を空ける。
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable

import requests

from .plans import Plan

API_BASE = "https://api.jquants.com/v2"
USER_AGENT = "jpstock-analyzer/0.1"


class JQuantsError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


class PlanNotSupportedError(JQuantsError):
    """契約プランでは利用できないエンドポイント/期間へのアクセス (HTTP 403)。"""


class RateLimiter:
    """最低リクエスト間隔を守るだけの単純なリミッタ。"""

    def __init__(
        self,
        requests_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = 60.0 / requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self._last + self.interval - now
            if remaining > 0:
                self._sleep(remaining)
                now += remaining
        self._last = now


def _fmt(d: date | str) -> str:
    return d.strftime("%Y-%m-%d") if isinstance(d, date) else d


class JQuantsClient:
    def __init__(
        self,
        api_key: str,
        plan: Plan,
        *,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.plan = plan
        self._session = session or requests.Session()
        self._session.headers.update({"x-api-key": api_key, "User-Agent": USER_AGENT})
        self._limiter = rate_limiter or RateLimiter(plan.requests_per_minute)
        self._timeout = timeout
        self._max_retries = max_retries

    # ---- 低レベル -------------------------------------------------------
    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        for attempt in range(self._max_retries + 1):
            self._limiter.wait()
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except requests.RequestException as e:
                raise JQuantsError(f"通信エラー: {e}") from e
            if resp.status_code == 429 and attempt < self._max_retries:
                # レート超過: 少し長めに待って再試行
                time.sleep(self._limiter.interval * (attempt + 2))
                continue
            if resp.ok:
                return resp.json()
            detail = _error_message(resp)
            if resp.status_code == 403:
                raise PlanNotSupportedError(
                    f"{path}: 契約プラン({self.plan.label})では利用できない可能性があります: {detail}", 403
                )
            if resp.status_code == 401:
                raise JQuantsError(f"認証に失敗しました。APIキーを確認してください: {detail}", 401)
            raise JQuantsError(f"{path}: HTTP {resp.status_code}: {detail}", resp.status_code)
        raise JQuantsError(f"{path}: レート制限により取得できませんでした", 429)

    def _get_all(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """pagination_key をたどって data 配列を連結する。"""
        query = {k: v for k, v in params.items() if v not in (None, "")}
        rows: list[dict[str, Any]] = []
        while True:
            payload = self._request(path, query)
            rows.extend(payload.get("data", []))
            key = payload.get("pagination_key")
            if not key:
                return rows
            query["pagination_key"] = key

    # ---- エンドポイント -------------------------------------------------
    def equities_master(self, code: str) -> list[dict[str, Any]]:
        """上場銘柄一覧 /equities/master"""
        return self._get_all("/equities/master", {"code": code})

    def daily_bars(self, code: str, date_from: date | str, date_to: date | str) -> list[dict[str, Any]]:
        """株価四本値 /equities/bars/daily"""
        return self._get_all("/equities/bars/daily", {"code": code, "from": _fmt(date_from), "to": _fmt(date_to)})

    def fin_summary(self, code: str) -> list[dict[str, Any]]:
        """財務情報サマリ (決算短信) /fins/summary"""
        return self._get_all("/fins/summary", {"code": code})

    def valuation(self, code: str, date_from: date | str, date_to: date | str) -> list[dict[str, Any]]:
        """バリュエーション指標 /equities/valuation"""
        return self._get_all("/equities/valuation", {"code": code, "from": _fmt(date_from), "to": _fmt(date_to)})

    def dividends(self, code: str) -> list[dict[str, Any]]:
        """配当金情報 /fins/dividend (Premium)"""
        return self._get_all("/fins/dividend", {"code": code})


def _error_message(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message", resp.text))
    except ValueError:
        return resp.text
