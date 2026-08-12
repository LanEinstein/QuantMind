"""Fetch public Douyin metadata and one temporary video at a time."""

from __future__ import annotations

import importlib.util
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode

import httpx

from scripts.yeren_corpus.models import VideoItem, VideoMetadata

LOGGER = logging.getLogger(__name__)
DOUYIN_API = "https://www.douyin.com/aweme/v1/web/aweme/post/"
DOUYIN_DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/90.0.4430.212 Safari/537.36"
)
CHINA_TZ = timezone(timedelta(hours=8))


def _load_signer(signer_root: Path) -> type[Any]:
    """Load the maintained upstream signer without vendoring its GPL source."""
    path = signer_root / "crawlers/douyin/web/abogus.py"
    if not path.is_file():
        raise FileNotFoundError(
            f"未找到抖音签名器: {path}。请先按 README 克隆下载工具。"
        )
    spec = importlib.util.spec_from_file_location("quantmind_yeren_abogus", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载抖音签名器: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(type[Any], getattr(module, "ABogus"))


def load_chrome_cookies() -> CookieJar:
    """Use the owner's logged-in session while keeping cookie values in memory."""
    import browser_cookie3  # type: ignore[import-untyped]

    cookies = cast(CookieJar, browser_cookie3.chrome(domain_name=".douyin.com"))
    if not any(cookie.name == "sessionid" for cookie in cookies):
        raise RuntimeError("Chrome 当前没有已登录的抖音会话，请先在浏览器登录。")
    return cookies


def _common_params() -> dict[str, Any]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "from_user_page": "1",
        "locate_query": "false",
        "need_time_list": "1",
        "pc_libra_divert": "Windows",
        "publish_video_strategy_type": "2",
        "round_trip_time": "0",
        "show_live_replay_strategy": "1",
        "time_list_query": "0",
        "whale_cut_token": "",
        "update_version_code": "170400",
        "msToken": "",
    }


def _page_params(sec_uid: str, cursor: int, count: int) -> dict[str, Any]:
    return {
        **_common_params(),
        "max_cursor": cursor,
        "count": count,
        "sec_user_id": sec_uid,
    }


def _first_url(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    urls = value.get("url_list")
    if not isinstance(urls, list):
        return ""
    return next((url for url in urls if isinstance(url, str) and url), "")


def normalize_aweme(item: dict[str, Any]) -> VideoItem:
    """Reduce a large API record to stable research metadata and a CDN URL."""
    aweme_id = str(item["aweme_id"])
    description = str(item.get("desc") or "")
    first_description_line = next(iter(description.splitlines()), "")
    title = str(item.get("preview_title") or first_description_line or aweme_id)
    hashtags = tuple(
        dict.fromkeys(
            str(extra["hashtag_name"])
            for extra in item.get("text_extra", ())
            if isinstance(extra, dict) and extra.get("hashtag_name")
        )
    )
    create_time = int(item["create_time"])
    statistics = item.get("statistics") or {}
    author = item.get("author") or {}
    video = item.get("video") or {}
    download_url = _first_url(video.get("play_addr_h264")) or _first_url(
        video.get("play_addr")
    )
    metadata = VideoMetadata(
        aweme_id=aweme_id,
        title=title,
        description=description,
        hashtags=hashtags,
        create_time=create_time,
        published_at=datetime.fromtimestamp(create_time, tz=CHINA_TZ).isoformat(),
        duration_ms=int(item.get("duration") or video.get("duration") or 0),
        digg_count=int(statistics.get("digg_count") or 0),
        comment_count=int(statistics.get("comment_count") or 0),
        collect_count=int(statistics.get("collect_count") or 0),
        share_count=int(statistics.get("share_count") or 0),
        play_count=int(statistics.get("play_count") or 0),
        author_nickname=str(author.get("nickname") or ""),
        author_douyin_id=str(author.get("unique_id") or ""),
        source_url=f"https://www.douyin.com/video/{aweme_id}",
    )
    return VideoItem(metadata=metadata, download_url=download_url)


class DouyinClient:
    """Keep all Douyin requests IPv4-only and deliberately low frequency."""

    def __init__(
        self,
        sec_uid: str,
        signer_root: Path,
        *,
        delay_range: tuple[float, float] = (5.0, 8.0),
        cookie_loader: Callable[[], CookieJar] = load_chrome_cookies,
    ) -> None:
        self.sec_uid = sec_uid
        self.signer = _load_signer(signer_root)
        self.delay_range = delay_range
        self.cookie_loader = cookie_loader
        transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            cookies=cookie_loader(),
            transport=transport,
            timeout=30,
            follow_redirects=True,
        )

    def __enter__(self) -> DouyinClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def _signed_url(self, endpoint: str, params: dict[str, Any]) -> str:
        signature = quote(self.signer().get_value(params), safe="")
        return f"{endpoint}?{urlencode(params)}&a_bogus={signature}"

    def _get_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.get(self._signed_url(endpoint, params))
        if response.status_code == 403:
            LOGGER.warning("抖音接口返回 403，等待 10 秒并刷新 Chrome 会话后重试一次")
            time.sleep(10)
            self.client.cookies.update(self.cookie_loader())
            response = self.client.get(self._signed_url(endpoint, params))
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _fetch_page(self, cursor: int, count: int) -> dict[str, Any]:
        return self._get_json(DOUYIN_API, _page_params(self.sec_uid, cursor, count))

    def fetch_detail(self, aweme_id: str) -> VideoItem:
        """Refresh the CDN URL immediately before its one permitted download."""
        payload = self._get_json(
            DOUYIN_DETAIL_API,
            {**_common_params(), "aweme_id": aweme_id},
        )
        if payload.get("status_code") != 0 or not payload.get("aweme_detail"):
            status_code = payload.get("status_code")
            raise RuntimeError(f"抖音作品详情失败: status_code={status_code}")
        return normalize_aweme(payload["aweme_detail"])

    def fetch_catalog(self, count: int = 18) -> list[VideoItem]:
        """Follow the newest-to-oldest API cursor, then return chronological items."""
        cursor = 0
        items: dict[str, VideoItem] = {}
        page_number = 0
        while True:
            payload = self._fetch_page(cursor, count)
            if payload.get("status_code") != 0:
                status_code = payload.get("status_code")
                raise RuntimeError(f"抖音主页接口失败: status_code={status_code}")
            page_number += 1
            for raw_item in payload.get("aweme_list") or ():
                video = normalize_aweme(raw_item)
                items[video.metadata.aweme_id] = video
            LOGGER.info("已采集主页第 %s 页，去重后 %s 条", page_number, len(items))
            if not payload.get("has_more"):
                break
            next_cursor = int(payload["max_cursor"])
            if next_cursor == cursor:
                raise RuntimeError("抖音主页游标未前进，停止以免无限请求")
            cursor = next_cursor
            time.sleep(random.uniform(*self.delay_range))
        return sorted(items.values(), key=lambda item: item.metadata.create_time)

    def download(self, item: VideoItem, destination: Path) -> None:
        """Stream one video so the temporary file never shares disk with the next."""
        if not item.download_url:
            raise RuntimeError(f"作品 {item.metadata.aweme_id} 没有可下载的视频地址")
        with self.client.stream("GET", item.download_url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type:
                aweme_id = item.metadata.aweme_id
                raise RuntimeError(f"作品 {aweme_id} 下载返回了网页而非视频")
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    output.write(chunk)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
