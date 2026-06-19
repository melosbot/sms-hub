"""日志配置单测(core/infra/logging_setup.py)。"""
import logging
import logging.config

from core.infra import logging_setup


def test_build_log_config_is_valid():
    """build_log_config() 必须能被 dictConfig 接受,结构完整。"""
    cfg = logging_setup.build_log_config()
    logging.config.dictConfig(cfg)  # 不抛错
    assert cfg["root"]["handlers"] == ["default"]
    assert cfg["root"]["level"] == logging_setup._LEVEL
    assert cfg["loggers"]["httpx"]["level"] == logging.WARNING
    # uvicorn 与应用共用同一个 handler → 格式统一
    assert cfg["loggers"]["uvicorn"]["handlers"] == ["default"]


def test_no_uvicorn_access_logger():
    """访问日志由 access_log=False + 错误中间件处理,不在 dictConfig 里配 uvicorn.access。"""
    cfg = logging_setup.build_log_config()
    assert "uvicorn.access" not in cfg["loggers"]


def test_level_follows_log_level(monkeypatch):
    """根级别随 LOG_LEVEL 变化。"""
    monkeypatch.setattr(logging_setup, "_LEVEL", logging.DEBUG)
    cfg = logging_setup.build_log_config()
    assert cfg["root"]["level"] == logging.DEBUG
    assert cfg["loggers"]["uvicorn"]["level"] == logging.DEBUG
