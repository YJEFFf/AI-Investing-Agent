import io
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import pandas_ta as pta
from matplotlib.gridspec import GridSpec

logger = logging.getLogger(__name__)
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import platform
if platform.system() == "Darwin":
    plt.rcParams["font.family"] = "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False


def render_daily_chart(
    df: pd.DataFrame,
    stock_name: str,
    stock_code: str,
) -> bytes:
    """일봉 60일 캔들스틱 차트를 PNG bytes로 반환.

    df: 최소 60행, 컬럼 [date, open, high, low, close, volume]
    지표는 전체 df로 계산 후 마지막 60행만 시각화 (EMA60 NaN 방지)
    """
    # 지표를 전체 df에서 계산해야 EMA60이 올바르게 표시됨
    close_full = df["close"].astype(float)
    high_full  = df["high"].astype(float)
    low_full   = df["low"].astype(float)
    vol_full   = df["volume"].astype(float)

    ema20_full   = pta.ema(close_full, length=20)
    ema60_full   = pta.ema(close_full, length=60)
    bb_full      = pta.bbands(close_full, length=20, std=2)
    rsi_full     = pta.rsi(close_full, length=14)
    vol_ma20_full = vol_full.rolling(20).mean()

    bb_upper_col = next(c for c in bb_full.columns if c.startswith("BBU_"))
    bb_mid_col   = next(c for c in bb_full.columns if c.startswith("BBM_"))
    bb_lower_col = next(c for c in bb_full.columns if c.startswith("BBL_"))

    # 마지막 60행으로 슬라이싱
    df = df.tail(60).reset_index(drop=True)
    n = len(df)
    x = list(range(n))

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    open_ = df["open"].astype(float)
    vol   = df["volume"].astype(float)

    ema20    = ema20_full.tail(n).reset_index(drop=True)
    ema60    = ema60_full.tail(n).reset_index(drop=True)
    bb       = bb_full.tail(n).reset_index(drop=True)
    rsi      = rsi_full.tail(n).reset_index(drop=True)
    vol_ma20 = vol_ma20_full.tail(n).reset_index(drop=True)

    fig = plt.figure(figsize=(14, 8), facecolor="#0d0d0d")
    gs  = GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.05)
    ax_price  = fig.add_subplot(gs[0])
    ax_volume = fig.add_subplot(gs[1], sharex=ax_price)
    ax_rsi    = fig.add_subplot(gs[2], sharex=ax_price)

    # 캔들스틱
    for i in x:
        o, h, l, c = open_.iloc[i], high.iloc[i], low.iloc[i], close.iloc[i]
        color = "#26a69a" if c >= o else "#ef5350"
        ax_price.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)
        body_h = max(abs(c - o), close.mean() * 0.001)
        rect = mpatches.Rectangle(
            (i - 0.38, min(o, c)), 0.76, body_h,
            color=color, zorder=3,
        )
        ax_price.add_patch(rect)

    # 오버레이
    ax_price.plot(x, ema20, color="#ffeb3b", linewidth=1.3, label="EMA20", zorder=4)
    ax_price.plot(x, ema60, color="#ff9800", linewidth=1.3, label="EMA60", zorder=4)
    ax_price.plot(x, bb[bb_upper_col], color="#90caf9", linewidth=0.9, linestyle="--", label="BB Upper", zorder=4)
    ax_price.plot(x, bb[bb_mid_col],   color="#90caf9", linewidth=0.7, linestyle=":",  alpha=0.5, zorder=4)
    ax_price.plot(x, bb[bb_lower_col], color="#90caf9", linewidth=0.9, linestyle="--", label="BB Lower", zorder=4)
    ax_price.fill_between(x, bb[bb_upper_col], bb[bb_lower_col], alpha=0.04, color="#90caf9")

    # 거래량
    vol_colors = ["#26a69a" if close.iloc[i] >= open_.iloc[i] else "#ef5350" for i in x]
    ax_volume.bar(x, vol, color=vol_colors, width=0.8, alpha=0.75)
    ax_volume.plot(x, vol_ma20, color="#ffeb3b", linewidth=0.9)

    # RSI
    ax_rsi.plot(x, rsi, color="#ce93d8", linewidth=1.0, label="RSI(14)")
    ax_rsi.axhline(70, color="#ef5350", linewidth=0.7, linestyle="--")
    ax_rsi.axhline(50, color="#888888", linewidth=0.4, linestyle=":")
    ax_rsi.axhline(30, color="#26a69a", linewidth=0.7, linestyle="--")
    ax_rsi.fill_between(x, rsi, 70, where=(rsi >= 70), alpha=0.15, color="#ef5350")
    ax_rsi.fill_between(x, rsi, 30, where=(rsi <= 30), alpha=0.15, color="#26a69a")
    ax_rsi.set_ylim(0, 100)

    # X축 레이블 (8개 간격)
    tick_pos = list(range(0, n, max(1, n // 8)))
    ax_rsi.set_xticks(tick_pos)
    ax_rsi.set_xticklabels(
        [str(df["date"].iloc[i])[:10] for i in tick_pos],
        rotation=30, fontsize=7,
    )

    # 스타일링
    title = f"{stock_name} ({stock_code})  Daily 60  EMA20/60 · BB · RSI"
    ax_price.set_title(title, color="white", fontsize=10, pad=6)
    for ax in [ax_price, ax_volume, ax_rsi]:
        ax.set_facecolor("#131722")
        ax.tick_params(colors="#aaaaaa", labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")
    ax_price.legend(fontsize=7, loc="upper left", facecolor="#1e1e1e", labelcolor="white")
    ax_price.tick_params(labelbottom=False)
    ax_volume.tick_params(labelbottom=False)
    ax_volume.set_ylabel("Vol", color="#aaaaaa", fontsize=7)
    ax_rsi.set_ylabel("RSI", color="#aaaaaa", fontsize=7)
    ax_price.set_ylabel("Price", color="#aaaaaa", fontsize=7)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()
