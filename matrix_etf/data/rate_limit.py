"""tickflow 免费档限速（60/min）应对：识别限流错误并按退避策略自动重试。

免费服务对请求频率有硬限制（约 60 次/分钟，按来源 IP 计），当 ETF / A 股 / 美股
多条线在同一台机器上并发拉数（尤其首次全量回填）时，很容易触发
「请求频率超限 (60/min)，请 XXXms 后重试」。

过去的做法是一遇限流就放弃当日同步、直接用本地历史数据。本模块提供带指数退避的
重试封装：**限流不是终点而是等一等再来**，从而尽最大努力把当日数据拉全，只有在
多次重试仍失败时才交由上层决定是否降级。
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# 识别「限流 / 频率超限」类错误的关键词（tickflow 免费档 + 通用 HTTP 429 语义）
_RATE_LIMIT_MARKERS = (
    "频率超限",
    "请求过于频繁",
    "限流",
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
    "60/min",
    "/min",
)

# 从错误信息中解析建议的等待时间，如「请 452ms 后重试」/「retry after 2s」。
_MS_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_S_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b", re.IGNORECASE)


def is_rate_limit_error(exc: BaseException) -> bool:
    """判断异常是否为限流 / 频率超限类错误。"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


def suggested_delay_seconds(exc: BaseException) -> float | None:
    """从错误信息里解析服务端建议的等待秒数；解析不到返回 None。"""
    msg = str(exc)
    m = _MS_HINT.search(msg)
    if m:
        return float(m.group(1)) / 1000.0
    m = _S_HINT.search(msg)
    if m:
        return float(m.group(1))
    return None


def call_with_retry(
    func: Callable[[], T],
    *,
    attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    logger: logging.Logger | None = None,
    what: str = "请求",
) -> T:
    """调用 ``func``；遇到限流或临时错误时按退避重试，直至成功或尝试用尽。

    退避策略：
      - **限流错误**：等待时间取「服务端建议值」与「指数退避 base_delay·2ⁿ」中的较大者，
        再叠加抖动，封顶 ``max_delay``。这样在被别的进程抢占额度时会逐步拉长等待，
        而不是按服务端那句乐观的「452ms 后重试」反复空转。
      - **其它错误**（网络抖动等）：按指数退避重试，给瞬时故障一个自愈机会。

    Args:
        func: 无参可调用对象，通常用 lambda 包裹实际的网络请求。
        attempts: 最大尝试次数（含首次）。
        base_delay: 指数退避基准秒数。
        max_delay: 单次等待上限秒数。
        logger: 可选日志器，用于记录每次重试。
        what: 出现在日志里的操作描述，便于定位是哪一步在重试。

    Returns:
        ``func()`` 的返回值。

    Raises:
        最后一次尝试抛出的异常（尝试用尽后原样抛出，交由上层降级处理）。
    """
    attempts = max(1, int(attempts))
    last_exc: BaseException | None = None

    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == attempts - 1:
                break

            rate_limited = is_rate_limit_error(exc)
            backoff = min(base_delay * (2**attempt), max_delay)
            if rate_limited:
                hinted = suggested_delay_seconds(exc) or 0.0
                delay = min(max(hinted, backoff), max_delay)
            else:
                delay = backoff
            delay += random.uniform(0.0, base_delay)  # 抖动，避免多进程同步重试

            if logger is not None:
                kind = "限流" if rate_limited else "错误"
                logger.warning(
                    f"{what} 遇到{kind}（第 {attempt + 1}/{attempts} 次尝试），"
                    f"{delay:.2f}s 后重试：{exc}"
                )
            time.sleep(delay)

    assert last_exc is not None  # 循环至少执行一次，last_exc 必被赋值
    raise last_exc
