"""pytest 公共配置:让测试能直接 import core 包。
config.py 在 import 时会 mkdir DATA_DIR,先指到临时目录。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="hub-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
