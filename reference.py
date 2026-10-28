#!/usr/bin/env python3
"""Build a real Pine Script function index from TradingView's reference.

The site's "Interactive Reference Manual" has, for a year, searched eight
hand-written functions while printing "1247 functions" above them, with invented
"popularity" percentages and bookmarks that reset on reload. The reference itself
is a JavaScript app with no server-rendered function list, so nothing could be
read from it with plain HTTP - which is presumably why it was faked.

Chrome's headless shell renders it. `--dump-dom` waits for the app to boot and
prints the live DOM, which then parses with the standard library. 694 entries
come out of the v6 reference: 475 functions, 162 variables, 21 operators, 20
types, 16 keywords.

  python3 reference.py --version v6            # -> dist/pine-script-reference-v6.json
  python3 reference.py --version v6 --stats    # counts per namespace, no file

The JSON is what the page renders, so its numbers are the real ones: the count
printed above the search box is `len(entries)`, and each row's syntax and one-line
description are what TradingView publishes.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
SHELLS = ("~/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-mac-*/chrome-headless-shell")
# The reference app for the version, and the anchor prefix each entry answers on.
BASES = {
    "v6": "https://www.tradingview.com/pine-script-reference/v6/",
    "v5": "https://www.tradingview.com/pine-script-reference/v5/",
    "v4": "https://www.tradingview.com/pine-script-reference/v4/",
}
KINDS = ("fun", "var", "op", "type", "kw")

# `data-legacy-id` sits between the id and the class on most entries, so the
# attributes in between are skipped rather than assumed absent.
ITEM_START = re.compile(r'id="(' + "|".join(KINDS) + r')_([^"]+)"[^>]*class="tv-pine-reference-item[^"]*"')
H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
SYNTAX = re.compile(r'<pre[^>]*tv-pine-reference-item__syntax[^>]*>(.*?)</pre>', re.S)
TEXT = re.compile(r'<div class="tv-pine-reference-item__text[^"]*"[^>]*>(.*?)</div>', re.S)


def chrome() -> str:
    import glob
    found = sorted(glob.glob(os.path.expanduser(SHELLS)))
    if not found:
        raise SystemExit("no chrome-headless-shell under ~/.cache/puppeteer")
    return found[-1]


def dump(url: str) -> str:
    r = subprocess.run(
        [chrome(), "--headless", "--disable-gpu", "--no-sandbox", "--dump-dom",
         "--virtual-time-budget=25000", url],
        capture_output=True, text=True)
    if r.returncode != 0 or len(r.stdout) < 100_000:
        raise SystemExit(f"render failed ({r.returncode}): {r.stderr[-300:]}")
    return r.stdout


def plain(fragment: str) -> str:
    """Text out of one element, with the spacing a reader expects."""
    t = re.sub(r"<br\s*/?>", " ", fragment)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def trim_sentence(text: str, limit: int) -> str:
    """Cut at a sentence end, else at a word, and mark the cut.

    A bare slice leaves 58 of the 935 descriptions ending mid-word with nothing to
    say they were cut (`fun_array.fill` ended "up to but "), which reads as a
    broken sentence rather than a shortened one.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if end >= limit // 2:
        return head[: end + 1]
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip(",;: ") + "\u2026"


def namespace(name: str) -> str:
    """`ta.sma` is in `ta`; a bare `alert` is a top-level built-in."""
    return name.split(".")[0] if "." in name else "built-in"


def extract(version: str) -> list[dict]:
    base = BASES[version]
    dom = dump(base)
    starts = list(ITEM_START.finditer(dom))
    # Each entry is emitted twice, and the second copy is nested a few hundred
    # bytes into the first, so "up to the next item" is not a block. Take the
    # longest run that no other entry's start interrupts.
    entries: dict[str, dict] = {}
    for i, m in enumerate(starts):
        kind, anchor = m.group(1), m.group(2)
        key = f"{kind}_{anchor}"
        end = min(len(dom), m.start() + 20000)
        for later in starts[i + 1:]:
            if f"{later.group(1)}_{later.group(2)}" != key:
                end = later.start()
                break
        block = dom[m.start():end]
        if key in entries and len(block) <= entries[key]["_len"]:
            continue
        if key in entries:
            del entries[key]
        h3 = H3.search(block)
        pre = SYNTAX.search(block)
        name = plain(h3.group(1)) if h3 else anchor
        syntax = plain(pre.group(1)) if pre else ""
        desc = ""
        tm = TEXT.search(block)
        if tm:
            desc = trim_sentence(plain(tm.group(1)), 280)
        key = f"{kind}_{anchor}"
        if key in entries:                     # the sidebar repeats each entry
            continue
        # The header carries the display name: "ta.sma()" for a function, "syminfo"
        # for a variable. Strip the call parens so the index reads like an index.
        display = re.sub(r"\(\)$", "", name)
        entries[key] = {
            "_len": len(block),
            "id": key,
            "name": display,
            "kind": kind,
            "namespace": namespace(display),
            "syntax": syntax,
            "description": desc,
            "url": f"{base}#{key}",
        }
    for e in entries.values():
        e.pop("_len", None)
    return sorted(entries.values(), key=lambda e: (e["namespace"], e["name"].lower()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v6", choices=sorted(BASES))
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    entries = extract(a.version)
    by_kind: dict[str, int] = {}
    by_ns: dict[str, int] = {}
    for e in entries:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        by_ns[e["namespace"]] = by_ns.get(e["namespace"], 0) + 1
    print(f"{a.version}: {len(entries)} entries {by_kind}", flush=True)
    print("namespaces:", ", ".join(f"{k}({v})" for k, v in sorted(by_ns.items())), flush=True)

    if a.stats:
        return 0
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"pine-script-reference-{a.version}.json"
    payload = {
        "version": a.version,
        "source": BASES[a.version],
        "entries": entries,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} B)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
