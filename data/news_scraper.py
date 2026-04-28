import asyncio
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 10.0


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: datetime
    source: str = ""


class NaverNewsScraper:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_stock_news(self, stock_code: str, max_items: int = 5) -> list[NewsItem]:
        """종목별 네이버 금융 뉴스 수집"""
        url = (
            f"https://finance.naver.com/item/news_news.naver"
            f"?code={stock_code}&page=1&sm=title_entity_id.basic"
        )
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"News fetch failed for {stock_code}: {e}")
            return []

        return self._parse_stock_news(resp.text, max_items)

    async def fetch_market_news(self, max_items: int = 10) -> list[NewsItem]:
        """네이버 금융 경제 뉴스 수집 (시장 전체)"""
        url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
        try:
            async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Market news fetch failed: {e}")
            return []

        return self._parse_market_news(resp.text, max_items)

    async def fetch_new_stock_news(self, stock_code: str, max_items: int = 5) -> list[NewsItem]:
        """이미 처리한 뉴스 제외하고 새 뉴스만 반환"""
        all_news = await self.fetch_stock_news(stock_code, max_items * 2)
        new_news = [n for n in all_news if n.url not in self._seen_urls]
        for n in new_news:
            self._seen_urls.add(n.url)
        return new_news[:max_items]

    def clear_seen(self) -> None:
        """장 종료 후 캐시 초기화"""
        self._seen_urls.clear()

    def _parse_stock_news(self, html: str, max_items: int) -> list[NewsItem]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        table = soup.find("table", class_="type5")
        if not table:
            return items

        for row in table.find_all("tr"):
            title_td = row.find("td", class_="title")
            date_td = row.find("td", class_="date")
            if not title_td or not date_td:
                continue

            a_tag = title_td.find("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href
            date_str = date_td.get_text(strip=True)
            published_at = self._parse_date(date_str)

            items.append(NewsItem(title=title, url=full_url, published_at=published_at))
            if len(items) >= max_items:
                break

        return items

    def _parse_market_news(self, html: str, max_items: int) -> list[NewsItem]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        for a_tag in soup.select("ul.realtimeNewsList li dl dt a"):
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            full_url = f"https://finance.naver.com{href}" if href.startswith("/") else href
            items.append(NewsItem(title=title, url=full_url, published_at=datetime.now()))
            if len(items) >= max_items:
                break
        return items

    def _parse_date(self, date_str: str) -> datetime:
        try:
            # 네이버 금융 날짜 형식: "2024.04.28 09:30" 또는 "09:30" (당일)
            date_str = date_str.strip()
            if len(date_str) <= 5:
                today = datetime.now().strftime("%Y.%m.%d")
                date_str = f"{today} {date_str}"
            return datetime.strptime(date_str, "%Y.%m.%d %H:%M")
        except Exception:
            return datetime.now()


news_scraper = NaverNewsScraper()
