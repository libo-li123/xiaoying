#!/usr/bin/env python3
"""A small, respectful web crawler for pages you are allowed to fetch."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PERSONAL_FIELD_HINTS = {
    "name",
    "full_name",
    "real_name",
    "username",
    "gender",
    "sex",
    "age",
    "birthday",
    "birthdate",
    "phone",
    "mobile",
    "email",
    "id_card",
    "identity",
    "address",
    "姓名",
    "名字",
    "用户名",
    "性别",
    "年龄",
    "生日",
    "出生日期",
    "电话",
    "手机",
    "邮箱",
    "身份证",
    "证件",
    "地址",
}


@dataclass
class CrawlConfig:
    url: str
    item_selector: str
    fields: dict[str, str]
    next_selector: str | None = None
    max_pages: int = 1
    delay: float = 1.0
    timeout: float = 20.0
    retries: int = 2
    backoff: float = 0.5
    respect_robots: bool = True
    allow_personal_data: bool = False
    redact_personal_data: bool = False


class Crawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        validate_personal_fields(config.fields, config.allow_personal_data)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "RespectfulCrawler/1.0 (+https://example.com/contact)",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self.session.mount("http://", self._retry_adapter())
        self.session.mount("https://", self._retry_adapter())
        self.robots_cache: dict[str, RobotFileParser] = {}

    def _retry_adapter(self) -> HTTPAdapter:
        retry = Retry(
            total=self.config.retries,
            connect=self.config.retries,
            read=self.config.retries,
            status=self.config.retries,
            backoff_factor=self.config.backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("HEAD", "GET", "OPTIONS"),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        return HTTPAdapter(max_retries=retry)

    def allowed_by_robots(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        if robots_url not in self.robots_cache:
            parser = RobotFileParser(robots_url)
            try:
                response = self.session.get(robots_url, timeout=self.config.timeout)
                response.raise_for_status()
            except requests.RequestException:
                # If robots.txt cannot be reached, fail closed.
                return False
            parser.parse(response.text.splitlines())
            self.robots_cache[robots_url] = parser
        return self.robots_cache[robots_url].can_fetch(self.session.headers["User-Agent"], url)

    def fetch(self, url: str) -> BeautifulSoup:
        if not self.allowed_by_robots(url):
            raise PermissionError(f"robots.txt disallows or is unavailable for: {url}")
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _extract_value(item: Any, selector: str, page_url: str) -> Any:
        attribute = None
        if "::attr(" in selector and selector.endswith(")"):
            selector, attribute = selector.rsplit("::attr(", 1)
            attribute = attribute[:-1]
        node = item.select_one(selector)
        if not node:
            return ""
        if not attribute:
            return node.get_text(" ", strip=True)
        value = node.get(attribute, "")
        if attribute in {"href", "src"} and value:
            return urljoin(page_url, value)
        return value

    def crawl(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        current_url = self.config.url
        visited: set[str] = set()

        for page_number in range(self.config.max_pages):
            if not current_url or current_url in visited:
                break
            visited.add(current_url)
            print(f"[{page_number + 1}/{self.config.max_pages}] {current_url}", file=sys.stderr)

            soup = self.fetch(current_url)
            for item in soup.select(self.config.item_selector):
                row: dict[str, Any] = {}
                for name, selector in self.config.fields.items():
                    value = self._extract_value(item, selector, current_url)
                    if self.config.redact_personal_data and is_personal_field(name):
                        value = redact_value(name, value)
                    row[name] = value
                if row not in results:
                    results.append(row)

            if not self.config.next_selector:
                break
            next_node = soup.select_one(self.config.next_selector)
            href = next_node.get("href") if next_node else None
            current_url = urljoin(current_url, href) if href else ""
            if current_url and page_number + 1 < self.config.max_pages:
                time.sleep(self.config.delay)

        return results


class BaiduSearcher:
    """Fetches ordinary Baidu web-search result pages without bypassing controls."""

    def __init__(self, keyword: str, max_pages: int = 1, delay: float = 2.0) -> None:
        self.keyword = keyword
        self.max_pages = max(1, max_pages)
        self.delay = max(1.0, delay)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def search(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(self.max_pages):
            url = f"https://www.baidu.com/s?wd={quote_plus(self.keyword)}&pn={page * 10}"
            print(f"[百度 {page + 1}/{self.max_pages}] {url}", file=sys.stderr)
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            if "安全验证" in text or "验证码" in text:
                raise PermissionError("百度要求安全验证，程序不会尝试绕过验证。请稍后再试。")

            items = soup.select("div.result, div.c-container")
            for item in items:
                title_node = item.select_one("h3")
                link_node = title_node.select_one("a") if title_node else item.select_one("a")
                if not title_node or not link_node:
                    continue
                href = link_node.get("href", "")
                row = {
                    "title": title_node.get_text(" ", strip=True),
                    "url": self._resolve_url(href),
                    "summary": self._summary(item),
                    "source": self._source(item),
                }
                if row["title"] and row["url"] and row not in rows:
                    rows.append(row)
            if page + 1 < self.max_pages:
                time.sleep(self.delay)
        return rows

    @staticmethod
    def _resolve_url(href: str) -> str:
        if not href:
            return ""
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        target = query.get("url", [href])[0]
        return unquote(target)

    @staticmethod
    def _summary(item: Any) -> str:
        for selector in ("div.c-span-last", ".c-abstract", ".content-right_8Zs40"):
            node = item.select_one(selector)
            if node:
                return node.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _source(item: Any) -> str:
        node = item.select_one(".c-color-gray, .c-color-t, .c-showurl")
        return node.get_text(" ", strip=True) if node else ""


def parse_fields(values: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"字段格式错误，应为 名称=CSS选择器: {value}")
        name, selector = value.split("=", 1)
        if not name.strip() or not selector.strip():
            raise ValueError(f"字段不能为空: {value}")
        fields[name.strip()] = selector.strip()
    return fields


def is_personal_field(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
    return any(hint in normalized for hint in PERSONAL_FIELD_HINTS)


def personal_fields(fields: dict[str, str]) -> list[str]:
    return [name for name in fields if is_personal_field(name)]


def validate_personal_fields(fields: dict[str, str], allow_personal_data: bool) -> None:
    detected = personal_fields(fields)
    if detected and not allow_personal_data:
        names = ", ".join(detected)
        raise ValueError(
            "检测到疑似个人信息字段："
            f"{names}。如你确认已获得授权并符合法律/平台规则，请添加 --allow-personal-data。"
        )


def redact_value(field_name: str, value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    normalized = field_name.strip().lower()
    if "email" in normalized or "邮箱" in normalized:
        name, sep, domain = value.partition("@")
        if not sep:
            return mask_middle(value)
        return f"{mask_middle(name)}@{domain}"
    if any(key in normalized for key in ("phone", "mobile", "电话", "手机")):
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) >= 7:
            return f"{digits[:3]}****{digits[-4:]}"
    if any(key in normalized for key in ("gender", "sex", "性别")):
        return value
    return mask_middle(value)


def mask_middle(value: str) -> str:
    if len(value) <= 1:
        return "*"
    if len(value) == 2:
        return value[0] + "*"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def save_rows(rows: list[dict[str, Any]], output: str, fmt: str) -> None:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        return
    fieldnames = list(rows[0]) if rows else []
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def demo_config(name: str, max_pages: int, delay: float) -> tuple[CrawlConfig, str]:
    demos = {
        "books": CrawlConfig(
            url="https://books.toscrape.com/",
            item_selector="article.product_pod",
            fields={
                "title": "h3 a::attr(title)",
                "price": ".price_color",
                "availability": ".availability",
                "url": "h3 a::attr(href)",
            },
            next_selector="li.next a",
            max_pages=max_pages,
            delay=delay,
        ),
        "quotes": CrawlConfig(
            url="https://quotes.toscrape.com/",
            item_selector=".quote",
            fields={
                "quote": ".text",
                "author": ".author",
                "tags": ".tags",
            },
            next_selector="li.next a",
            max_pages=max_pages,
            delay=delay,
        ),
    }
    if name not in demos:
        raise ValueError(f"未知 demo：{name}，可选 books 或 quotes")
    default_output = f"{name}.json"
    return demos[name], default_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通用、限速、遵守 robots.txt 的网页爬虫")
    parser.add_argument("url", nargs="?", help="起始 URL（通用模式使用）")
    parser.add_argument("--demo", choices=("books", "quotes"), help="直接抓取可测试站点：books 或 quotes")
    parser.add_argument("--baidu", help="百度搜索关键词，例如 --baidu Python教程")
    parser.add_argument("-i", "--item", help="每条数据的 CSS 选择器（通用模式必填）")
    parser.add_argument(
        "-f",
        "--field",
        action="append",
        required=False,
        help="字段定义，可重复：名称=CSS选择器，例如 title=.title",
    )
    parser.add_argument("--next", dest="next_selector", help="下一页链接的 CSS 选择器")
    parser.add_argument("-m", "--max-pages", type=int, default=1)
    parser.add_argument("-d", "--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0, help="单次请求超时时间（秒）")
    parser.add_argument("--retries", type=int, default=2, help="临时网络错误/限流的重试次数")
    parser.add_argument("--backoff", type=float, default=0.5, help="重试退避系数")
    parser.add_argument("--ignore-robots", action="store_true", help="不检查 robots.txt（仅在你确认有权限时使用）")
    parser.add_argument(
        "--allow-personal-data",
        action="store_true",
        help="允许提取姓名、性别等个人信息字段（仅在已获授权且合规时使用）",
    )
    parser.add_argument(
        "--redact-personal-data",
        action="store_true",
        help="对识别到的个人信息字段进行脱敏输出，适合测试和样例数据",
    )
    parser.add_argument("-o", "--output", default="data.json")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.demo:
            config, default_output = demo_config(args.demo, max(1, args.max_pages), max(0.0, args.delay))
            config.timeout = max(1.0, args.timeout)
            config.retries = max(0, args.retries)
            config.backoff = max(0.0, args.backoff)
            config.respect_robots = not args.ignore_robots
            config.allow_personal_data = args.allow_personal_data
            config.redact_personal_data = args.redact_personal_data
            rows = Crawler(config).crawl()
            output = args.output if args.output != "data.json" else default_output
            save_rows(rows, output, args.format)
            print(f"完成：{args.demo} demo 抓取 {len(rows)} 条，已保存到 {output}")
            return 0
        if args.baidu:
            rows = BaiduSearcher(args.baidu, args.max_pages, args.delay).search()
            save_rows(rows, args.output, args.format)
            print(f"完成：百度抓取 {len(rows)} 条，已保存到 {args.output}")
            return 0
        if not args.url or not args.item or not args.field:
            raise ValueError("通用模式需要提供 URL、--item 和至少一个 --field；百度模式使用 --baidu")
        config = CrawlConfig(
            url=args.url,
            item_selector=args.item,
            fields=parse_fields(args.field),
            next_selector=args.next_selector,
            max_pages=max(1, args.max_pages),
            delay=max(0.0, args.delay),
            timeout=max(1.0, args.timeout),
            retries=max(0, args.retries),
            backoff=max(0.0, args.backoff),
            respect_robots=not args.ignore_robots,
            allow_personal_data=args.allow_personal_data,
            redact_personal_data=args.redact_personal_data,
        )
        rows = Crawler(config).crawl()
        save_rows(rows, args.output, args.format)
        print(f"完成：抓取 {len(rows)} 条，已保存到 {args.output}")
        return 0
    except (requests.RequestException, PermissionError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
