"""device.py 纯函数测试:短信段数估算(与固件 UCS2 单元算法一致)。"""
from core.device import client as device


def test_estimate_parts():
    assert device.estimate_parts("a" * 70) == 1
    assert device.estimate_parts("a" * 71) == 2
    assert device.estimate_parts("汉" * 70) == 1      # CJK 计 1 单元
    assert device.estimate_parts("😀" * 35) == 1      # 35×2 = 70 单元
    assert device.estimate_parts("😀" * 36) == 2      # 72 单元 → 2 段
    assert device.estimate_parts("a" * 300) == 5
