"""tickflow 客户端工厂：ETF 与股票数据引擎共享的客户端创建逻辑。

免费服务（TickFlow.free）提供历史日 K、标的信息与标的池；
配置 ``TICKFLOW_API_KEY`` 后升级为完整服务。
"""

from __future__ import annotations

import logging


def create_tickflow_client(api_key: str, logger: logging.Logger | None = None):
    """根据 API Key 创建 tickflow 客户端。

    Args:
        api_key: tickflow API Key，为空时使用免费服务。
        logger: 可选日志器，用于记录当前使用的服务档位。

    Returns:
        已初始化的 ``tickflow.TickFlow`` 客户端实例。
    """
    from tickflow import TickFlow

    if api_key:
        if logger is not None:
            logger.info("使用 tickflow 完整服务（API Key 已配置）")
        return TickFlow(api_key=api_key)

    if logger is not None:
        logger.info("使用 tickflow 免费服务")
    return TickFlow.free()
