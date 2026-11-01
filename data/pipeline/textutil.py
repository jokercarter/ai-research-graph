"""Tokenizer shared by the index builder; frontend/search.js implements the same rules."""
import re

STOP = set("""a an the and or of for in on to with by from at as is are be been being this that these those it its into
over under via we our us they their them can may might will would should could than then thus also such which who whom
whose what when where while how not no nor but if so do does did done have has had having more most less least very
using use based towards toward without within across between among about each per all any both other some only own same
further here there up down out off again once i you he she his her paper propose proposed approach method results show""".split())
TOKEN = re.compile(r"[a-z0-9]+")


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and (w[-3] in "sxz" or w[-4:-2] in ("ch", "sh")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.endswith("us") and not w.endswith("is"):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    out = []
    for w in TOKEN.findall((text or "").lower()):
        if w in STOP or len(w) < 2 or (w.isdigit() and len(w) < 4):
            continue
        out.append(stem(w))
    return out


def bigrams(toks: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def fnv1a(s: str) -> int:
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h
