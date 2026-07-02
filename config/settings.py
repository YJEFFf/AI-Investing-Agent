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

    # Anthropic
    anthropic_api_key: str

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # KRX 계정 (pykrx 로그인용)
    krx_id: str = ""
    krx_pw: str = ""

    # Database
    database_url: str = "mysql+aiomysql://root:password@localhost:3306/trading"

    # Risk Parameters
    max_drawdown_pct: float = Field(default=0.15)
    daily_loss_cap: float = Field(default=0.05)
    max_position_pct: float = Field(default=0.25)
    default_stop_pct: float = Field(default=0.02)
    swing_stop_pct: float = Field(default=0.08)
    partial_tp_trail_pct: float = Field(default=0.08)

    @property
    def is_paper(self) -> bool:
        return self.kis_env == "vps"

    @property
    def kis_base_url(self) -> str:
        if self.is_paper:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"

    @property
    def kis_ws_url(self) -> str:
        if self.is_paper:
            return "ws://ops.koreainvestment.com:31000"
        return "ws://ops.koreainvestment.com:21000"


settings = Settings()
