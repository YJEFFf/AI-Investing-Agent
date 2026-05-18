from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # KIS API — 국장 모의투자용 (openapivts)
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str
    kis_env: str = "vps"  # vps=모의매매, prod=실전

    # KIS API — 미장 실전용 (openapi, 해외주식 모의 환경 없음)
    kis_real_app_key: str = ""
    kis_real_app_secret: str = ""
    kis_real_account_no: str = ""  # 실전 계좌번호 (모의 계좌번호와 다름)
    us_seed_usd: float = 0.0  # 미장 시드 (잔고 API 실패 시 폴백)

    # Anthropic
    anthropic_api_key: str

    # Telegram — 한국장
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Telegram — 미국장 (별도 봇 + 별도 채팅방)
    telegram_us_bot_token: str = ""
    telegram_us_chat_id: str = ""

    # Database
    database_url: str = "mysql+aiomysql://root:password@localhost:3306/trading"

    # Risk Parameters
    max_drawdown_pct: float = Field(default=0.15)
    daily_loss_cap: float = Field(default=0.05)
    max_open_positions: int = Field(default=7)
    max_position_pct: float = Field(default=0.25)
    default_stop_pct: float = Field(default=0.02)
    swing_stop_pct: float = Field(default=0.05)       # KR 폴백 손절 비율
    us_swing_stop_pct: float = Field(default=0.08)    # US 폴백 손절 비율 (일간 변동폭 감안)
    partial_tp_trail_pct: float = Field(default=0.08)  # partial TP 후 trailing 비율 확대

    @property
    def is_paper(self) -> bool:
        return self.kis_env == "vps"

    @property
    def kis_base_url(self) -> str:
        if self.is_paper:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"

    @property
    def kis_real_base_url(self) -> str:
        return "https://openapi.koreainvestment.com:9443"

    @property
    def kis_ws_url(self) -> str:
        if self.is_paper:
            return "ws://ops.koreainvestment.com:31000"
        return "ws://ops.koreainvestment.com:21000"


settings = Settings()
