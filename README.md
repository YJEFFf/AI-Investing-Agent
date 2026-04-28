# AIA — AI 자동 투자 에이전트

한국투자증권(KIS) API 기반 국내 주식 자동매매 시스템입니다.  
멀티 AI 에이전트가 기술적 분석, 뉴스, 시장 레짐을 종합해 매매를 결정합니다.

---

## 주요 기능

- **멀티에이전트 전략** — TA / 뉴스(Claude AI) / 시장 / 리스크 에이전트가 병렬로 분석 후 합의 기반 결정
- **시장 레짐 감지** — KOSPI 데이터로 추세/횡보/고변동/하락장 분류, 하락장 신규매수 자동 차단
- **리스크 관리** — half-Kelly 포지션 사이징, ATR·트레일링·고정 손절, 일손실 3% / MDD 10% 한도
- **스케줄링** — 08:30 장전준비 → 09:00 매매 시작 → 15:30 종료 자동화
- **백테스팅** — look-ahead bias 없는 bar-by-bar 엔진, 수수료·슬리피지 반영
- **모의/실전 전환** — `.env`의 `KIS_ENV` 값 하나로 전환

---

## 아키텍처

```
WebSocket Tick
     ↓
1분봉 완성
     ↓
TA 점수 계산 (45점 미만 → skip)
     ↓
TA Agent │ News Agent │ Market Agent  (병렬)
                   ↓
             Risk Agent
                   ↓
          Decision Agent → 매수 / 스킵
                   ↓
          Order Manager → KIS API
```

---

## 기술 스택

- Python 3.12 / asyncio
- `anthropic` — 뉴스 분석 LLM (Claude claude-sonnet-4-6, prompt caching)
- `pandas-ta` — 기술적 지표
- `SQLAlchemy` + `aiomysql` — 거래 기록 (MySQL)
- `APScheduler` — 장 스케줄 관리
- `httpx` / `websockets` — KIS REST / WebSocket API

---

## 설치 및 실행

```bash
# 의존성 설치
pip install -e .

# 환경변수 설정
cp .env.example .env
# .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, ANTHROPIC_API_KEY 입력

# 모의매매 실행 (KIS_ENV=vps)
python scripts/run_paper.py

# 백테스트 실행
python scripts/run_backtest.py
```

---

## 환경변수

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` | KIS API 앱키 |
| `KIS_APP_SECRET` | KIS API 시크릿 |
| `KIS_ACCOUNT_NO` | 계좌번호 |
| `KIS_ENV` | `vps` (모의) / `prod` (실전) |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |
| `DATABASE_URL` | MySQL 연결 URL |

---

## 리스크 파라미터 (기본값)

| 항목 | 값 |
|------|----|
| 최대 동시 보유 종목 | 5개 |
| 종목당 최대 비중 | 10% |
| 기본 손절 | 2% |
| 일일 손실 한도 | 3% |
| 최대 낙폭(MDD) | 10% |
