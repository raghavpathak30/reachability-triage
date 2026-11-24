import re

_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control(value: str) -> str:
    """Remove C0/C1 control characters (ESC, DEL, 0x80-0x9F), keeping tab and newline."""
    return _CONTROL.sub("", value)


def strip_control_line(value: str) -> str:
    """Single-line variant: tab and newline are replaced with a space too."""
    return strip_control(value.replace("\t", " ").replace("\n", " "))
