"""统一日志配置。

应用日志与 uvicorn 共用同一套 formatter/handler,避免两套格式并存。根因:uvicorn.run
会用它自带的 LOGGING_CONFIG 覆盖此前任何 logging 配置,所以必须经 log_config= 把我们的
配置交给它。访问日志通过 uvicorn access_log=False 关闭,改由 main.py 的错误中间件按需
记录(仅 ≥400),正常 2xx 完全静默。
"""
import logging
import logging.config

from core.infra import config

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LEVEL = getattr(logging, config.LOG_LEVEL, logging.INFO)


def build_log_config() -> dict:
    """构造 dictConfig:根 logger 与 uvicorn.* 共用 default handler,格式统一。"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": _FORMAT, "datefmt": _DATEFMT},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": _LEVEL, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": _LEVEL, "propagate": False},
            "httpx": {"handlers": ["default"], "level": logging.WARNING, "propagate": False},
        },
        "root": {"handlers": ["default"], "level": _LEVEL},
    }


def configure() -> None:
    """应用日志配置(import 时调一次,确保 uvicorn.run 之前的日志也有正确格式)。"""
    logging.config.dictConfig(build_log_config())
