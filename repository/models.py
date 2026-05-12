from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Boolean, Text, Enum
)
from sqlalchemy.orm import DeclarativeBase
import enum


class Base(DeclarativeBase):
    pass


class TradeStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class SignalAction(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    SKIP = "skip"


class Signal(Base):
    """차트 분석 신호 로그"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(2), default="KR", nullable=False)  # KR | US
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    stock_code = Column(String(10), nullable=False)
    ta_score = Column(Float, nullable=False)
    chart_verdict = Column(String(10))
    chart_confidence = Column(Float)
    risk_level = Column(String(10))
    final_action = Column(String(10), nullable=False)
    position_size_pct = Column(Float)
    reasoning = Column(Text)


class Trade(Base):
    """실제 체결된 거래 기록"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(2), default="KR", nullable=False)  # KR | US
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50))
    status = Column(String(10), default=TradeStatus.OPEN, nullable=False)
    entry_at = Column(DateTime, nullable=False)
    entry_price = Column(Integer, nullable=False)
    entry_qty = Column(Integer, nullable=False)
    exit_at = Column(DateTime)
    exit_price = Column(Integer)
    stop_price = Column(Integer)
    stop_type = Column(String(20))   # fixed | trailing | atr
    profit_loss = Column(Integer)
    profit_loss_pct = Column(Float)
    exit_reason = Column(String(50))  # stop_loss | take_profit | signal | emergency
    signal_id = Column(Integer)


class Position(Base):
    """현재 보유 포지션 (실시간 상태)"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(2), default="KR", nullable=False)  # KR | US
    stock_code = Column(String(10), nullable=False, unique=True)
    stock_name = Column(String(50))
    qty = Column(Integer, nullable=False)
    avg_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    stop_type = Column(String(20), default="fixed")
    target_price = Column(Float)
    trail_pct = Column(Float)       # trailing stop 비율 (LLM 손절가에서 계산)
    highest_price = Column(Float)   # 트레일링 스탑용 고점 추적
    trade_type = Column(String(10), default="swing")  # day | swing
    opened_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailyPnL(Base):
    """일별 손익 요약"""
    __tablename__ = "daily_pnl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True)  # YYYY-MM-DD
    starting_balance = Column(Integer)
    ending_balance = Column(Integer)
    realized_pnl = Column(Integer, default=0)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    news_summary = Column(Text)
