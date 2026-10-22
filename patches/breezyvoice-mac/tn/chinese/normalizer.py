"""Minimal stand-in for WeTextProcessing's Chinese normalizer (pynini has no macOS arm64 wheel).
Reads numbers in Taiwanese Mandarin style; everything else passes through unchanged."""
import re

_D = "零一二三四五六七八九"
_U = ["", "十", "百", "千"]
_BIG = ["", "萬", "億", "兆"]


def _four(n):
    s, zero = "", False
    for i in range(3, -1, -1):
        d = n // (10 ** i) % 10
        if d == 0:
            zero = bool(s)
        else:
            if zero:
                s += "零"
                zero = False
            s += _D[d] + _U[i]
    return s


def int2zh(n):
    if n == 0:
        return "零"
    groups, out = [], ""
    while n:
        groups.append(n % 10000)
        n //= 10000
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            if out and not out.endswith("零"):
                out += "零"
            continue
        if out and g < 1000 and not out.endswith("零"):
            out += "零"
        out += _four(g) + _BIG[i]
    out = out.rstrip("零")
    if out.startswith("一十"):
        out = out[1:]
    return out.replace("二千", "兩千").replace("二萬", "兩萬").replace("二億", "兩億")


def _num(m):
    t = m.group(0)
    pct = t.endswith("%")
    t = t.rstrip("%").replace(",", "")
    if "." in t:
        a, b = t.split(".", 1)
        s = int2zh(int(a or 0)) + "點" + "".join(_D[int(c)] for c in b)
    elif len(t) >= 2 and t.startswith("0"):
        s = "".join(_D[int(c)] for c in t)  # phone numbers, codes
    else:
        s = int2zh(int(t))
    return "百分之" + s if pct else s


def _year(m):
    return "".join(_D[int(c)] for c in m.group(1)) + "年"


class Normalizer:
    def __init__(self, *args, **kwargs):
        pass

    def normalize(self, text):
        text = re.sub(r"(\d{4})年", _year, text)
        return re.sub(r"\d[\d,]*(?:\.\d+)?%?", _num, text)
