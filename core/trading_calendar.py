from datetime import date, datetime, timedelta
import holidays

_KR_HOLIDAYS = holidays.country_holidays("KR")

# holidays 패키지에서 누락된 KRX 휴장일 수동 등록
# 근로자의 날(5/1)은 일반 공휴일 목록에 없지만 KRX는 휴장
for _year in range(2024, 2035):
    _KR_HOLIDAYS[date(_year, 5, 1)] = "근로자의 날"

def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    return d not in _KR_HOLIDAYS


def last_analysis_cutoff_utc() -> datetime:
    """pending 신호 유효 기준: 마지막 거래일 16:00 KST (= 07:00 UTC, naive) 이후

    24시간 윈도우 대신 이 함수를 사용하면 주말/공휴일 후 월요일 00:00에
    금요일 신호가 만료되어 재분석이 실행되는 문제를 방지한다.
    """
    now_utc = datetime.utcnow()
    today = now_utc.date()
    # 오늘 16:00 KST = 07:00 UTC
    today_analysis_start = datetime(today.year, today.month, today.day, 7, 0, 0)
    if now_utc >= today_analysis_start and is_trading_day(today):
        return today_analysis_start
    # 아직 오늘 분석 시작 전이거나 비거래일 → 이전 거래일로 거슬러 올라감
    d = today - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, 7, 0, 0)


