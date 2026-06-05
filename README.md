# AIA — AI 자동 투자 에이전트

한국투자증권(KIS) API 기반 국내 주식 스윙 매매(2~10일 보유) 자동화 시스템입니다.  
Claude Vision이 일봉 차트 이미지를 직접 분석해 매수 적합성과 손절/목표 비율을 판단합니다.

---

## 매매 방식

**스윙 매매 전략** — 장 마감 후 차트 분석 → 익일 시가 매수 → 트레일링 스톱/목표가 청산

| 단계 | 시간 (KST) | 내용 |
|------|-----------|------|
| 장 종료 | 15:30 | 대기열 초기화 |
| 장후 정산 | 15:35 | 당일 실현 손익 집계 → 텔레그램 요약 |
| 장전 분석 | 16:00~익일 00:00 (매시) | pending 3개 미만이면 일봉 로드 → Claude Vision 차트 분석 → pending 신호 DB 저장 |
| 장 시작 | 09:00 | pending 신호 실행 → 현재가 조회 → 포지션 사이징 → 시가 매수 |
| 장중 모니터링 | 09:00~15:30 | 1분마다 보유 종목 현재가 조회 → 트레일링 스톱/익절 체크 |

---

## 아키텍처

```
[장전 분석 — 16:00~익일 00:00 매시, pending 3개 미만일 때]
일봉 OHLCV (120일) 로드
        ↓
TA 엔진 → TA 복합 점수 계산 (25점 미만 → 스킵)
        ↓
Claude Vision ← 일봉 차트 PNG (캔들 + EMA20/60 + BB + RSI + 거래량)
        ↓
AgentOpinion {verdict, confidence, stop_pct, target_pct}
        ↓
Risk Agent (포지션 수 / 일손실 / MDD 체크)
        ↓
confidence ≥ 0.50 → pending 신호 DB 저장 (재시작 후 복원 가능)

[장 시작 — 09:00]
pending 신호 복원 (메모리 없으면 DB 조회)
        ↓
현재가 조회 → 2,000,000 × confidence 포지션 사이징 → 시가 매수
        ↓
Position DB 저장 (stop_price, target_price, trail_pct — 모두 LLM 결정값)

[장중 1분 폴링]
현재가 ≥ target_price → 절반 익절 + 손익분기 stop 이동
현재가 ≤ stop_price  → 전량 손절
신고가 경신          → trailing stop 상향 (trail_pct 고정 아님, LLM 결정)
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
| `regime_detector.py` | KOSPI 일봉으로 레짐 분류 (TRENDING / RANGING / HIGH_VOL / DOWN) |

### 핵심 흐름 (`core/`)
| 파일 | 역할 |
|------|------|
| `orchestrator.py` | 장 전체 흐름 조율, 매수 신호 실행, 손절 체크 |
| `scheduler.py` | APScheduler — 장전/개장/마감/재분석 스케줄링 (KST) |
| `notifier.py` | 텔레그램 매수/매도/요약 알림 |

### 리스크 (`risk/`)
| 파일 | 역할 |
|------|------|
| `position_sizer.py` | 2,000,000 × confidence 포지션 사이징 (가용잔고 90% 캡) |
| `stop_loss.py` | trailing / fixed 손절 계산 |
| `portfolio_guard.py` | 일손실 / MDD 한도 관리 |

### 데이터 / 실행
| 파일 | 역할 |
|------|------|
| `data/fetcher.py` | KIS REST → 일봉 OHLCV, KOSPI 데이터 (parquet 캐시) |
| `data/screener.py` | 거래대금 상위 200 → TA 스크리닝 → 상위 종목 (auto 모드) |
| `execution/order_manager.py` | 매수 / 전량매도 / 부분매도 실행 + DB 기록 |
| `kis/` | KIS REST 클라이언트, 인증, 잔고/시세 |
| `repository/` | SQLAlchemy 모델 (Trade, Position, Signal), 비동기 쿼리 |

---

## 차트 분석 기준 (Claude Vision)

**매수 신호 (1개 이상 충족)**
- EMA20 > EMA60 또는 골든크로스 임박
- 볼린저밴드 하단~중단 구간 반등 징조
- RSI 30~65 구간 (과매도 해소 또는 중립)
- 거래량 동반 양봉
- 망치형 / 역망치형 / 장악형 양봉 캔들 패턴

**스킵 기준**
- EMA 명확한 하락 배열 + RSI 70 이상
- 거래량 없는 음봉 3개 이상 연속
- 지지선 명확한 이탈

LLM은 분석 결과와 함께 `stop_pct`(손절 비율)와 `target_pct`(목표 비율)를 직접 반환합니다.  
이 값들은 코드에서 덮어쓰지 않으며 물리적 범위(stop: 2~12%, target: 3~15%)만 보장합니다.

---

## 포지션 사이징

종목당 투자 금액 = **2,000,000원 × confidence**

```
budget = 2,000,000 × confidence
budget = min(budget, 가용잔고 × 0.90)   # 잔고 90% 캡
qty    = budget // 현재가
```

| confidence | 투자금액 |
|-----------|---------|
| 0.50 | 약 1,000,000원 |
| 0.62 | 약 1,240,000원 |
| 0.72 | 약 1,440,000원 |
| 1.00 | 2,000,000원 (최대) |

---

## 익절 / 손절 로직

```
목표가(target_price) 도달
  └─ qty > 1 → 절반 익절 + stop → avg_price(손익분기)로 이동, trail_pct 유지
  └─ qty = 1 → 전량 익절

stop_price 하향 돌파 → 전량 손절

신고가 경신 → stop_price = max(현재_stop, 신고가 × (1 - trail_pct))
```

`trail_pct`는 LLM이 분석 시 반환한 `stop_pct` 값으로 설정되며, 익절 이후에도 변경되지 않습니다.

---

## Pending 신호 처리

장전 분석 결과는 DB에 `pending=True`로 저장됩니다.

- **재시작 복원**: 프로세스 재시작 시 DB에서 24시간 이내 pending 신호를 자동 복원
- **분석 조건**: 16:00~익일 00:00 사이 매시 정각, pending 신호 3개 미만이면 분석 실행
- **신호 만료**: 24시간 경과한 pending 신호는 장 후 정산 시 자동 만료 처리

---

## 리스크 파라미터

| 항목 | 값 | 환경변수 |
|------|-----|----------|
| 손절 비율 | LLM 결정 (2~12%) | — |
| 목표 비율 | LLM 결정 (3~15%) | — |
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
# .env에 필수값 입력

# 모의매매 (KIS_ENV=vps)
python scripts/run_paper.py

# 백테스트
python scripts/run_backtest.py
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
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | — |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | — |

---

## 테스트

```bash
pytest tests/ -v

pytest tests/test_stop_loss.py           # 손절 로직
pytest tests/test_order_manager.py       # 주문 실행
pytest tests/test_orchestrator_exit.py   # 익절/손절 트리거
pytest tests/test_position_sizer.py      # 포지션 사이징
```

테스트는 in-memory SQLite를 사용해 MySQL 없이 실행됩니다.

---

## 배포 (EC2)

```bash
# 코드 업데이트
git push
ssh aia "cd ~/trading_agent && git pull"

# 프로세스 재시작
ssh aia "pkill -f run_paper.py"
ssh aia "cd ~/trading_agent && source .venv/bin/activate && nohup python scripts/run_paper.py >> logs/paper.log 2>&1 &"

# 로그 확인
ssh aia "tail -f ~/trading_agent/logs/paper.log"
```
