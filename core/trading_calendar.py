from datetime import date, datetime
from zoneinfo import ZoneInfo
import holidays

_KR_HOLIDAYS = holidays.country_holidays("KR")

# holidays 패키지에서 누락된 KRX 휴장일 수동 등록
# 근로자의 날(5/1)은 일반 공휴일 목록에 없지만 KRX는 휴장
for _year in range(2024, 2035):
    _KR_HOLIDAYS[date(_year, 5, 1)] = "근로자의 날"

_US_HOLIDAYS = holidays.country_holidays("US", subdiv="NY")  # NYSE 기준


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    return d not in _KR_HOLIDAYS


def is_us_trading_day(d: date | None = None) -> bool:
    """NYSE 기준 미국 거래일 여부. 인자 생략 시 NY 현재 날짜 기준으로 판단."""
    if d is None:
        d = datetime.now(ZoneInfo("America/New_York")).date()
    if d.weekday() >= 5:
        return False
    return d not in _US_HOLIDAYS
