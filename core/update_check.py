"""
版本更新检测
从 GitHub 官方 API / 源站页面 / 用户自定义镜像检测最新版本。
支持后台线程检测，不阻塞 TUI 主流程；所有网络异常静默处理。
"""

import json
import re
import threading
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

from core.version import APP_VERSION, CHECK_TIMEOUT, DEFAULT_CHECK_URLS


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """
    解析版本号字符串为整数元组。
    兼容 "v1.2" / "1.2.0" / "1.2.0-beta"（忽略非数字尾缀）。
    """
    if not text or not isinstance(text, str):
        return None
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    """latest 是否比 current 更新。任一无法解析时返回 False。"""
    latest_v = parse_version(latest)
    current_v = parse_version(current)
    if not latest_v or not current_v:
        return False
    return latest_v > current_v


def _read_url(url: str, timeout: int):
    """读取 URL 内容，返回 (bytes, content_type, final_url)。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"ipmg/{APP_VERSION} (update-check)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(65536)
        content_type = response.headers.get("Content-Type", "") or ""
        final_url = response.geturl() or url
    return data, content_type, final_url


def _extract_version(data: bytes, content_type: str, final_url: str) -> Optional[Dict[str, str]]:
    """从响应中提取版本信息。"""
    text = data.decode("utf-8", errors="replace").strip()

    # 源站 /releases/latest：从重定向后的最终 URL 提取 tag（正文可能为空）
    match = re.search(r"/releases/tag/([^/?#]+)", final_url)
    if match:
        return {"version": match.group(1), "published_at": "", "body": ""}

    if not text:
        return None

    # GitHub releases API: JSON 含 tag_name / published_at / body
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("tag_name"):
            return {
                "version": obj["tag_name"],
                "published_at": obj.get("published_at") or "",
                "body": obj.get("body") or "",
            }
    except (ValueError, TypeError):
        pass

    # 镜像站纯文本：直接作为版本号
    if parse_version(text):
        return {"version": text, "published_at": "", "body": ""}

    return None


def check_latest(urls: Optional[Sequence[str]] = None, timeout: int = CHECK_TIMEOUT) -> Optional[Dict[str, str]]:
    """
    按序尝试检测源，返回 {"version", "published_at", "body"}。
    全部失败返回 None（调用方静默处理）。
    """
    for url in (urls or DEFAULT_CHECK_URLS):
        try:
            data, content_type, final_url = _read_url(url, timeout)
            info = _extract_version(data, content_type, final_url)
            if info and parse_version(info["version"]):
                info["url"] = url
                return info
        except Exception:
            continue
    return None


class UpdateChecker:
    """后台线程更新检测器（线程安全）。"""

    def __init__(self, urls: Optional[Sequence[str]] = None, timeout: int = CHECK_TIMEOUT):
        self._urls = list(urls) if urls else list(DEFAULT_CHECK_URLS)
        self._timeout = timeout
        self._result: Optional[Dict[str, str]] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """后台启动检测（幂等）。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        result = check_latest(self._urls, self._timeout)
        with self._lock:
            self._result = result

    def get_result(self) -> Optional[Dict[str, str]]:
        with self._lock:
            return self._result


# 全局默认检测器（供标题栏与手动检查使用）
default_checker = UpdateChecker()
