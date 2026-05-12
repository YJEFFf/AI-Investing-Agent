# AIA — AI 자동 투자 에이전트

한국투자증권(KIS) API 기반 국내/미국 주식 스윙 매매(2~10일 보유) 자동화 시스템입니다.  
Claude Vision이 일봉 차트 이미지를 직접 분석해 매수 적합성을 판단합니다.

---

## 매매 방식

**스윙 매매 전략** — 매일 장전에 차트 분석 → 시가 매수 → 목표가/트레일링 스톱으로 청산

### 국내주식 (KST 기준)

| 단계 | 시간 | 내용 |
|------|------|------|
| 장전 준비 | 08:30 | 일봉 120일 로드 → Claude Vision 차트 분석 → 매수 대기열 구성 |
| 장 시작 | 09:00 | 대기열 종목 현재가 확인 → 포지션 사이징 → 시가 매수 |
| 장중 모니터링 | 09:00~15:30 | 1분마다 보유 종목 현재가 조회 → 트레일링 스톱/익절 체크 |
| 장 종료 | 15:30 | 대기열 초기화 |
| 장후 정산 | 15:40 | 당일 실현 손익 집계 → 텔레그램 요약 |

### 미국주식 (America/New_York 기준, DST 자동 처리)

| 단계 | 시간 | 내용 |
|------|------|------|
| 장전 준비 | 09:00 NY | 일봉 120일 로드 → Claude Vision 차트 분석 → 매수 대기열 구성 |
| 장 시작 | 09:30 NY | 대기열 종목 현재가 확인 → 포지션 사이징 → 시가 매수 |
| 장중 모니터링 | 09:30~16:00 NY | 1분마다 보유 종목 현재가 조회 → 트레일링 스톱/익절 체크 |
| 장 종료 | 16:00 NY | 대기열 초기화 |
| 장후 정산 | 16:10 NY | 당일 실현 손익 집계 → 텔레그램 요약 |

---

## 아키텍처

```
[장전 준비]
일봉 OHLCV (120일) 로드
        ↓
TA 엔진 → TA 복합 점수 계산 (35점 미만 → 스킵)
        ↓
Claude Vision ← 일봉 차트 PNG (캔들 + EMA20/60 + BB + RSI + 거래량)
        ↓
AgentOpinion {verdict, confidence, stop_pct, target_pct}
        ↓
Risk Agent (포지션 수 / 일손실 / MDD 체크)
        ↓
confidence ≥ 0.50 → _pending_signals 등록

[장 시작]
현재가 조회 → half-Kelly 포지션 사이징 → 시가 매수
        ↓
Position DB 저장 (stop_price, target_price, trail_pct)

[장중 1분 폴링]
현재가 ≥ target_price → 절반 익절 + 손익분기 stop 이동
현재가 ≤ stop_price  → 전량 손절
신고가 경신          → trailing stop 상향
```

---

## 핵심 컴포넌트

### 전략 계층 (`strategy/`)
| 파일 | 역할 |
|------|------|
| `chart_renderer.py` | matplotlib으로 일봉 60일 캔들스틱 PNG 생성 |
| `agents/chart_agent.py` | Claude Sonnet 4.6 Vision 호출 → `{verdict, confidence, stop_pct, target_pct}` |
| `agents/risk_agent.py` | 포지션 수 / 일손실 / MDD 룰 기반 체크 |
| `agents/decision_agent.py` | `TradeSignal` dataclass 정의 |
| `multi_agent_strategy.py` | TA 필터 → 차트 분석 → 리스크 → 최종 `TradeSignal` 반환 |
| `ta_engine.py` | RSI / EMA / MACD / 볼린저밴드 수치 계산 |
| `signal_scorer.py` | TA 지표 가중 합산 → 0~100점 복합 점수 |
| `regime_detector.py` | KOSPI/NASDAQ 일봉으로 레짐 분류 (TRENDING / RANGING / HIGH_VOL / DOWN) |

### 핵심 흐름 (`core/`)
| 파일 | 역할 |
|------|------|
| `orchestrator.py` | 국내 장 전체 흐름 조율, 매수 신호 실행, 손절 체크 |
| `scheduler.py` | APScheduler — 국내 장전/개장/마감 스케줄링 (KST) |
| `notifier.py` | 국내 텔레그램 매수/매도/요약 알림 |
| `us_orchestrator.py` | 미국 장 전체 흐름 조율 |
| `us_scheduler.py` | APScheduler — 미국 장전/개장/마감 스케줄링 (America/New_York) |
| `us_notifier.py` | 미국 텔레그램 알림 |

### 리스크 (`risk/`)
| 파일 | 역할 |
|------|------|
| `position_sizer.py` | half-Kelly 포지션 사이징 |
| `stop_loss.py` | trailing / fixed 손절 계산 |
| `portfolio_guard.py` | 최대 종목 수 / 일손실 / MDD 한도 관리 |

### 데이터 / 실행
| 파일 | 역할 |
|------|------|
| `data/fetcher.py` | KIS REST → 일봉 OHLCV, KOSPI 데이터 (parquet 캐시) |
| `data/screener.py` | 거래대금 상위 200 → TA 스크리닝 → 상위 40종목 |
| `data/us_watchlist_updater.py` | NASDAQ 100 워치리스트 매주 월요일 자동 갱신 |
| `execution/order_manager.py` | 국내 매수 / 전량매도 / 부분매도 실행 + DB 기록 |
| `execution/us_order_manager.py` | 미국 매수 / 전량매도 / 부분매도 실행 + DB 기록 |
| `kis/` | KIS REST 클라이언트, 인증, 국내 잔고/시세 |
| `kis/overseas_*.py` | KIS 해외주식 클라이언트, 잔고/시세/주문 |
| `repository/` | SQLAlchemy 모델 (Trade, Position, Signal), 비동기 쿼리 |

---

## 차트 분석 기준 (Claude Vision)

**매수 신호 (1개 이상 충족)**
- EMA20 > EMA60 또는 골든크로스 임박
- 볼린저밴드 하단~중단 구간 반등 징조
- RSI 30~65 구간 (과매도 해소 또는 중립)
- 거래량 동반 양봉
- 망치형 / 역망치형 / 장악형 양봉 캔들 패턴

**스킵 기준 (아래 조건 동시 충족 시)**
- EMA 명확한 하락 배열 + RSI 70 이상
- 거래량 없는 음봉 3개 이상 연속
- 지지선 명확한 이탈

---

## 익절 / 손절 로직

```
목표가(target_price) 도달
  └─ qty > 최소단위 → 절반 익절 + stop → avg_price(손익분기), trail_pct 0.08로 확대
  └─ qty ≤ 최소단위 → 전량 익절

stop_price 하향 돌파 → 전량 손절 (stop_loss)

신고가 경신 → stop_price = max(현재_stop, new_high × (1 - trail_pct))
```

---

## 리스크 파라미터

| 항목 | 기본값 | 환경변수 |
|------|--------|----------|
| 최대 동시 보유 종목 | 7개 | `MAX_OPEN_POSITIONS` |
| 종목당 최대 비중 | 25% | `MAX_POSITION_PCT` |
| 국내 기본 손절 비율 | 5% | `SWING_STOP_PCT` |
| 미국 기본 손절 비율 | 8% | `US_SWING_STOP_PCT` |
| 절반 익절 후 trailing | 8% | — |
| 일일 손실 한도 | 5% | `DAILY_LOSS_CAP` |
| 최대 낙폭(MDD) | 15% | `MAX_DRAWDOWN_PCT` |
| 하락장(TRENDING_DOWN) | 신규 매수 차단 | — |

---

## 설치 및 실행

```bash
# 의존성 설치
pip install -e .

# 환경변수 설정
cp .env.example .env
# .env에 필수값 입력 (아래 표 참고)

# 모의매매 — 국내 (KIS_ENV=vps)
python scripts/run_paper.py

# 실전매매 — 국내
python scripts/run_live.py

# 실전매매 — 미국
python scripts/run_us_live.py

# 백테스트 — 국내
python scripts/run_backtest.py

# 백테스트 — 미국
python scripts/run_us_backtest.py
```

---

## 환경변수

| 변수 | 설명 | 필수 |
|------|------|------|
| `KIS_APP_KEY` | KIS API 앱키 | ✅ |
| `KIS_APP_SECRET` | KIS API 시크릿 | ✅ |
| `KIS_ACCOUNT_NO` | 계좌번호 | ✅ |
| `KIS_ENV` | `vps` (모의) / `prod` (실전) | ✅ |
| `ANTHROPIC_API_KEY` | Anthropic API 키 (Vision 분석) | ✅ |
| `DATABASE_URL` | MySQL 연결 URL | ✅ |
| `TELEGRAM_BOT_TOKEN` | 국내 텔레그램 봇 토큰 | — |
| `TELEGRAM_CHAT_ID` | 국내 텔레그램 채팅 ID | — |
| `TELEGRAM_US_BOT_TOKEN` | 미국 텔레그램 봇 토큰 | — |
| `TELEGRAM_US_CHAT_ID` | 미국 텔레그램 채팅 ID | — |

---

## 테스트

```bash
# 전체 테스트 실행 (55개)
pytest tests/ -v

# 개별 모듈
pytest tests/test_stop_loss.py              # 손절 로직
pytest tests/test_order_manager.py          # 국내 주문 실행
pytest tests/test_us_order_manager.py       # 미국 주문 실행
pytest tests/test_orchestrator_exit.py      # 국내 익절/손절 트리거
pytest tests/test_us_orchestrator_exit.py   # 미국 익절/손절 트리거
pytest tests/test_position_sizer.py         # 포지션 사이징
```

테스트는 in-memory SQLite를 사용해 MySQL 없이 실행됩니다.

---

## 배포 (EC2)

```bash
# 코드 업데이트
ssh aia "cd /home/ubuntu/trading_agent && git pull"

# 서비스 재시작
ssh aia "sudo systemctl restart aia-trading"

# 로그 확인
ssh aia "sudo journalctl -u aia-trading -f"
```
