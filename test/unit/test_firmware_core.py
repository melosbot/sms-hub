"""Compile and run the Arduino-independent firmware parser core."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_firmware_modem_core(tmp_path):
    binary = tmp_path / "test_modem_core"
    subprocess.run([
        "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "firmware"),
        str(ROOT / "firmware" / "modem_line_reader.cpp"),
        str(ROOT / "firmware" / "modem_policy.cpp"),
        str(ROOT / "test" / "firmware" / "test_modem_core.cpp"),
        "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)


def test_firmware_modem_concat(tmp_path):
    binary = tmp_path / "test_modem_concat"
    subprocess.run([
        "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "firmware"),
        str(ROOT / "test" / "firmware" / "test_modem_concat.cpp"),
        "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)


def test_firmware_modem_io_core(tmp_path):
    binary = tmp_path / "test_modem_io_core"
    subprocess.run([
        "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "firmware"),
        str(ROOT / "firmware" / "modem_io_core.cpp"),
        str(ROOT / "test" / "firmware" / "test_modem_io_core.cpp"),
        "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
