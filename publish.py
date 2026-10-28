#!/usr/bin/env python3
"""Rebuild the offline packages, publish them, and keep the page's numbers true.

The page at pinescriptstrategy.com/resources/pine-script prints a byte count, a
page count and a figure count per package, and a route serves those files from R2.
Both facts are declarations in the site repo
(`src/features/pine-script-resources/lib/offline-packages.ts`), so a refresh that
only replaces the bytes leaves the page lying about them.

  python3 publish.py refresh                      # scrape, build, upload, report
  python3 publish.py refresh --deploy             # ... then patch, test, build, ship
  python3 publish.py refresh --versions v6        # one version
  python3 publish.py refresh --skip-scrape        # rebuild from the local mirror

What it will not do: add a package the site does not declare. A new artifact is
reported as undeclared and left alone, because a file nobody listed is a file the
serving route would 404 anyway (the declaration is the route's allow-list).

Exit codes: 0 nothing drifted, 10 bytes changed (and, with --deploy, shipped),
1 an error. A cron job can tell "nothing to do" from "something changed".
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
KB = HERE / "knowledge_base"
SITE = Path.home() / "hermes-projects" / "pinescriptstrategy" / "app"
DECL = SITE / "src/features/pine-script-resources/lib/offline-packages.ts"
BUCKET = "pinescriptstrategy-attachments"
PREFIX = "docs-offline"
WRANGLER = ["npx", "wrangler"]

sys.path.insert(0, str(HERE))
import refresh  # noqa: E402 - the scraper and builder live in this repo


def sh(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"  FAILED: {' '.join(cmd)}\n{r.stdout[-800:]}\n{r.stderr[-800:]}", flush=True)
        raise SystemExit(1)
    return r


def declared() -> dict[str, dict]:
    """Parse the site's declaration: file -> the numbers it prints."""
    src = DECL.read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    for block in re.finditer(r"\{[^{}]*?file:\s*\"([^\"]+)\"[^{}]*?\}", src, re.S):
        body = block.group(0)
        def num(field: str) -> int | None:
            m = re.search(rf"{field}:\s*(\d+)", body)
            return int(m.group(1)) if m else None
        m = re.search(r'mirroredOn:\s*"([^"]+)"', body)
        out[block.group(1)] = {
            "bytes": num("bytes"), "pages": num("pages"), "figures": num("figures"),
            "mirroredOn": m.group(1) if m else None,
        }
    return out


def patch_declaration(file: str, *, bytes_: int, pages: int, figures: int, day: str) -> bool:
    """Rewrite one entry's numbers in place. Returns True if anything changed."""
    src = DECL.read_text(encoding="utf-8")
    m = re.search(rf'\{{[^{{}}]*?file:\s*"{re.escape(file)}"[^{{}}]*?\}}', src, re.S)
    if not m:
        raise SystemExit(f"{file} is not declared in {DECL}")
    block = m.group(0)
    new = block
    for field, value in (("bytes", bytes_), ("pages", pages), ("figures", figures)):
        new = re.sub(rf"({field}:\s*)\d+", rf"\g<1>{value}", new)
    new = re.sub(r'(mirroredOn:\s*")[^"]+(")', rf"\g<1>{day}\g<2>", new)
    if new == block:
        return False
    DECL.write_text(src[: m.start()] + new + src[m.end():], encoding="utf-8")
    return True


def upload(path: Path) -> None:
    ctype = "application/zip" if path.suffix == ".zip" else "application/pdf"
    sh([*WRANGLER, "r2", "object", "put", f"{BUCKET}/{PREFIX}/{path.name}",
        "--file", str(path), "--content-type", ctype, "--remote"], cwd=SITE)


def served_size(name: str) -> int:
    """Read the object back and count the bytes. A put that 'succeeded' is not a
    measurement; this is."""
    r = subprocess.run(
        [*WRANGLER, "r2", "object", "get", f"{BUCKET}/{PREFIX}/{name}", "--remote", "--pipe"],
        cwd=str(SITE), capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"readback failed for {name}: {r.stderr[-300:]!r}")
    return len(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["refresh"])
    ap.add_argument("--versions", default="v6,v5,v4")
    ap.add_argument("--skip-scrape", action="store_true")
    ap.add_argument("--force-build", action="store_true",
                    help="rebuild even when the mirrored pages are byte-identical to the last run")
    ap.add_argument("--force-fetch", action="store_true",
                    help="re-download every page instead of reading the on-disk cache. A scheduled "
                         "run MUST pass this: the cache makes a refresh a no-op that looks clean")
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--commit", action="store_true",
                    help="commit the declaration change (numbers only) and leave the deploy to a person")
    ap.add_argument("--quiet", action="store_true",
                    help="for a scheduled run: say nothing when nothing changed, one line when it did, "
                         "and exit 0 either way so a finding never reads as a broken job")
    a = ap.parse_args()

    def say(*line: str) -> None:
        if not a.quiet:
            print(*line, flush=True)

    # refresh.py prints its own per-page and per-figure progress, so silencing
    # this file's prints is not enough: under --quiet its stdout goes to a sink
    # as well, or a scheduled run delivers fourteen kilobytes of crawler chatter.
    sink = contextlib.redirect_stdout(io.StringIO()) if a.quiet else contextlib.nullcontext()

    versions = [v.strip() for v in a.versions.split(",") if v.strip()]
    known = declared()
    say(f"declared packages: {len(known)}")

    before: dict[str, str | None] = {}
    for v in versions:
        mp = KB / v / "manifest.json"
        before[v] = json.loads(mp.read_text())["content_hash"] if mp.exists() else None

    produced: dict[str, dict] = {}
    for v in versions:
        if not a.skip_scrape:
            say(f"--- scraping {v} ---")
            with sink:
                # force=… is what makes this a refresh. Without it every page comes
                # from .cache/ and a monthly job would read its own last download,
                # report "nothing moved" every month, and never see a TradingView edit.
                refresh.scrape(v, force=a.force_fetch)
        mpath = KB / v / "manifest.json"
        if not mpath.exists():
            say(f"  {v}: no mirror, skipped")
            continue
        manifest = json.loads(mpath.read_text())
        if (not a.force_build and before.get(v) == manifest.get("content_hash")
                and all((KB / v / "pages").rglob("*.html"))):
            say(f"  {v}: unchanged since the last mirror ({manifest['content_hash']}), "
                f"nothing rebuilt")
            continue
        say(f"--- building {v} ({manifest['pages_ok']} pages) ---")
        with sink:
            refresh.build_offline(v, manifest)
            refresh.build_pdf(v, manifest)
        figures = refresh.count_figures(v, manifest)
        for name in (f"pine-script-docs-{v}-offline.zip", f"pine-script-docs-{v}-manual.pdf"):
            path = DIST / name
            if path.exists():
                        produced[name] = {"pages": manifest["pages_ok"], "figures": figures,
                                  "bytes": path.stat().st_size,
                                  "day": refresh.fetched_day(manifest)}

    undeclared = [n for n in produced if n not in known]
    if undeclared:
        say(f"  UNDECLARED artifacts (not on the page, not uploaded): {undeclared}")

    changed: list[str] = []
    for name, info in produced.items():
        if name not in known:
            continue
        say(f"--- publishing {name} ({info['bytes']:,} B) ---")
        upload(DIST / name)
        served = served_size(name)
        if served != info["bytes"]:
            print(f"  SERVED SIZE MISMATCH: built {info['bytes']:,}, served {served:,}", flush=True)
            return 1
        if (known[name]["bytes"] != info["bytes"] or known[name]["pages"] != info["pages"]
                or known[name]["figures"] != info["figures"]
                or known[name]["mirroredOn"] != info["day"]):
            if patch_declaration(name, bytes_=info["bytes"], pages=info["pages"],
                                 figures=info["figures"], day=info["day"]):
                changed.append(name)

    if not changed:
        # Either the source had not moved (nothing was rebuilt, so the served
        # bytes and the printed numbers are the same ones), or a rebuild landed on
        # the same sizes. Both are silence.
        say("nothing drifted: the page's numbers still match the bytes")
        return 0

    say(f"declaration updated for: {changed}")
    # The gate before anything unattended ships: the declaration is what the
    # route's allow-list is built from, so a broken number is a broken route.
    sh(["npx", "vitest", "run", "src/test/pages/offlinePackages.test.ts"], cwd=SITE)

    if a.commit:
        # Numbers only, and only this file: the page's own copy, routes and tests
        # are a person's change, not a refresh. A commit here is what makes the
        # next deploy (anyone's) carry the true sizes.
        sh(["git", "add", str(DECL)], cwd=SITE)
        sh(["git", "-c", "user.name=Hermes", "-c", "user.email=hermes@localhost",
            "commit", "-m", f"resources: the offline page's numbers after a refresh ({len(changed)} packages)",
            "--", str(DECL)], cwd=SITE)
        say("committed the declaration; the deploy is still a person's step")

    if a.quiet:
        # A finding, not a failure: the scheduler delivers the line and the job
        # is healthy. Exit 1 is reserved for "this run broke".
        print(f"Pine Script mirror refreshed: {len(changed)} package(s) changed "
              f"({', '.join(changed)}). The page's numbers are committed; a deploy is owed.",
              flush=True)
        return 0

    if not a.deploy:
        print("not deployed (pass --deploy, or run the site's own deploy)", flush=True)
        return 10

    sh(["npm", "run", "build"], cwd=SITE)
    nobuild = SITE / "wrangler.nobuild.jsonc"
    src = (SITE / "wrangler.jsonc").read_text(encoding="utf-8")
    i = src.find('"build"')
    nobuild.write_text((src[:i].rstrip().rstrip(",") + "\n}\n") if i > 0 else src, encoding="utf-8")
    try:
        for env in ("staging", "production"):
            sh([*WRANGLER, "deploy", "--env", env, "--config", "wrangler.nobuild.jsonc"], cwd=SITE)
    finally:
        nobuild.unlink(missing_ok=True)
    print("shipped to staging and production", flush=True)
    return 0 if a.quiet else 10


if __name__ == "__main__":
    sys.exit(main())
