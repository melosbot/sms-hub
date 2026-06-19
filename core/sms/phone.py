"""Phone number normalization used by the hub.

Only mainland China mobile numbers are canonicalized from +86xxxxxxxxxxx to
xxxxxxxxxxx. Service numbers and other international numbers are kept as-is.
"""
import re

_MAINLAND_MOBILE_WITH_CC = re.compile(r"^\+86(1\d{10})$")


def canonicalize(phone: str) -> str:
    p = str(phone or "").strip().replace(" ", "").replace("-", "")
    m = _MAINLAND_MOBILE_WITH_CC.match(p)
    return m.group(1) if m else p
