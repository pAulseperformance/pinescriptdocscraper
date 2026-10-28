#!/usr/bin/env python3
"""Refresh the Pine Script manual mirror, then build the offline packages.

Why this exists next to scraper.py: scraper.py needs crawl4ai + Playwright, which
are not installed anywhere on this machine, and its BFS depth of 3 silently drops
pages that sit four clicks from welcome. The docs are server-rendered (Astro), so
plain HTTP gets the same bytes, and the sidebar nav lists the whole manual.

  python3 refresh.py scrape            # fetch, cache, write knowledge_base/<ver>/
  python3 refresh.py scrape --force    # ignore the cache
  python3 refresh.py build             # build dist/*.zip and dist/*.pdf
  python3 refresh.py all

Layout:
  knowledge_base/<ver>/pages/<slug>.html   cleaned content region, one per page
  knowledge_base/<ver>/pages/<slug>.txt    text only, for the search index
  knowledge_base/<ver>/manifest.json       url, title, sha256, bytes, fetched_at
  dist/pine-script-docs-<ver>-offline.zip  single-file offline manual + README
  dist/pine-script-docs-<ver>-manual.pdf   print/PDF edition
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from pathlib import Path

ROOT = "https://www.tradingview.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DELAY = 0.35                      # seconds between requests, politely
VERSIONS = {"v6": "/pine-script-docs", "v5": "/pine-script-docs/v5",
            "v4": "/pine-script-docs/v4", "v3": "/pine-script-docs/v3"}
HERE = Path(__file__).resolve().parent
KB = HERE / "knowledge_base"
DIST = HERE / "dist"
CACHE = HERE / ".cache"
IMG_CACHE = CACHE / "img"


# ---------------------------------------------------------------- fetching
def fetch(url: str, force: bool = False) -> bytes | None:
    """GET with a disk cache, so a rebuild never re-hammers TradingView."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    cached = CACHE / key
    if cached.exists() and not force:
        return cached.read_bytes()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en", "Accept-Encoding": "identity"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read()
            # the CDN gzips even when asked for identity, so trust the magic bytes
            if body[:2] == b"\x1f\x8b":
                import gzip
                body = gzip.decompress(body)
            cached.write_bytes(body)
            time.sleep(DELAY)
            return body
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                print(f"    {e.code} {url}", flush=True)
                return None
            if attempt == 2:
                print(f"    HTTP {e.code} after 3 tries: {url}", flush=True)
                return None
            time.sleep(2 + attempt * 2)
        except Exception as e:                     # noqa: BLE001 - transport
            if attempt == 2:
                print(f"    {type(e).__name__} after 3 tries: {url}", flush=True)
                return None
            time.sleep(2 + attempt * 2)
    return None


# ---------------------------------------------------------------- parsing
def content_region(doc: str) -> str | None:
    """The page body: <main class="content"> ... </main>."""
    i = doc.find('<main class="content')
    if i < 0:
        return None
    j = doc.find("</main>", i)
    if j < 0:
        return None
    return doc[i:j + 7]


def page_title(region: str, doc: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", region, re.S) or \
        re.search(r"<title>(.*?)</title>", doc, re.S)
    if not m:
        return "Untitled"
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()


def clean_region(region: str) -> str:
    """Drop navigation chrome, scripts and tracking attributes. Keep the prose."""
    out = region
    out = re.sub(r"<(script|style|noscript)\b.*?</\1>", "", out, flags=re.S | re.I)
    # breadcrumbs, the prev/next pagination cards, and the "on this page" rail
    out = re.sub(r'<div class="breadcrumbs".*?</div>', "", out, flags=re.S)
    out = re.sub(r'<div class="pagination-wrapper".*?</div>\s*</div>', "", out, flags=re.S)
    out = re.sub(r'<a [^>]*class="pagination-card".*?</a>', "", out, flags=re.S)
    out = re.sub(r'<nav[^>]*class="[^"]*toc[^"]*".*?</nav>', "", out, flags=re.S | re.I)
    out = re.sub(r'<div[^>]*class="[^"]*(?:toc|table-of-contents)[^"]*".*?</div>', "",
                 out, flags=re.S | re.I)
    # SVG icon markup is most of the bytes and none of the meaning
    out = re.sub(r"<svg\b.*?</svg>", "", out, flags=re.S | re.I)
    # inline event handlers, astro scope ids, and hidden edit links
    out = re.sub(r'\s(?:data-astro-cid-[a-z0-9]+|on[a-z]+|tabindex|aria-label)="[^"]*"', "", out)
    out = re.sub(r'<a [^>]*aria-label="(?:Edit|Copy)[^"]*"[^>]*>.*?</a>', "", out, flags=re.S)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def to_text(fragment: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|div|li|tr|h[1-6]|pre|table|aside)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()


def links_in(fragment: str, prefix: str) -> set[str]:
    found = set()
    for href in re.findall(r'href="(/pine-script-docs/[^"#?]*)"', fragment):
        if not href.startswith(prefix + "/") and href.rstrip("/") != prefix:
            continue
        # a version prefix must not swallow the other versions' trees
        rest = href[len(prefix):].lstrip("/")
        if prefix == "/pine-script-docs" and rest.split("/")[0] in ("v3", "v4", "v5"):
            continue
        if any(seg.startswith("_") for seg in rest.split("/")):
            continue
        if re.search(r"\.(?:css|js|svg|png|jpg|jpeg|webp|ico|json|xml|zip|pdf)$", href):
            continue
        found.add(href.rstrip("/") or href)
    return found


def slug_of(url_path: str, prefix: str) -> str:
    rest = url_path[len(prefix):].strip("/")
    return rest or "index"


# ---------------------------------------------------------------- scrape
def scrape(version: str, force: bool = False) -> dict:
    prefix = VERSIONS[version]
    out_dir = KB / version / "pages"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    queue = deque([(prefix + "/welcome/", 0)])
    seen: dict[str, dict] = {}
    while queue:
        path, depth = queue.popleft()
        if path in seen or depth > 4:
            continue
        url = ROOT + path
        body = fetch(url, force=force)
        if body is None:
            seen[path] = {"url": url, "ok": False}
            continue
        doc = body.decode("utf-8", "replace")
        region = content_region(doc)
        if region is None:
            seen[path] = {"url": url, "ok": False, "why": "no content region"}
            continue
        title = page_title(region, doc)
        clean = clean_region(region)
        slug = slug_of(path, prefix)
        page_path = out_dir / (slug + ".html")
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(clean, encoding="utf-8")
        text = to_text(clean)
        (out_dir / (slug + ".txt")).write_text(text, encoding="utf-8")
        seen[path] = {
            "url": url, "ok": True, "slug": slug, "title": title,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "html_bytes": len(clean.encode()), "text_bytes": len(text.encode()),
            "sha256": hashlib.sha256(clean.encode()).hexdigest()[:16],
            "depth": depth,
        }
        print(f"  {len(seen):>3} {slug:<52} {len(text.encode()):>8}B", flush=True)
        # the sidebar nav sits outside the content region and lists the whole
        # manual, so discovery reads the full document, not just the body
        for href in sorted(links_in(doc, prefix)):
            if href not in seen:
                queue.append((href if href.endswith("/") else href + "/", depth + 1))

    ok = {k: v for k, v in seen.items() if v.get("ok")}
    manifest = {
        "version": version, "prefix": prefix,
        "source": "TradingView Pine Script User Manual",
        "scraped_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "pages_ok": len(ok), "pages_failed": len(seen) - len(ok),
        "pages": [ok[k] for k in sorted(ok)],
        "failed": [v["url"] for k, v in seen.items() if not v.get("ok")],
    }
    (KB / version / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"\n  {version}: {len(ok)} pages ok, {len(seen) - len(ok)} failed -> {KB / version}",
          flush=True)
    return manifest


# ---------------------------------------------------------------- build
OFFLINE_CSS = """
:root{--ink:#1b1f2a;--mut:#5b6472;--line:#e3e6ec;--bg:#fff;--accent:#2962ff;--code:#f5f6f8}
*{box-sizing:border-box}
body{margin:0;font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header b{font-size:15px}
header .meta{color:var(--mut);font-size:12.5px}
.wrap{display:flex;align-items:flex-start;max-width:1400px;margin:0 auto}
nav{width:290px;flex:0 0 290px;border-right:1px solid var(--line);padding:16px 10px 60px;max-height:calc(100vh - 52px);overflow:auto;position:sticky;top:52px}
nav a{display:block;color:var(--ink);text-decoration:none;font-size:13.5px;padding:4px 8px;border-radius:5px}
nav a:hover{background:#f1f4ff}
nav a.on{background:#e8eeff;color:var(--accent);font-weight:600}
nav .grp{color:var(--mut);font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin:14px 8px 4px}
main{flex:1 1 auto;min-width:0;padding:26px 34px 120px}
h1{font-size:30px;line-height:1.25;margin:0 0 18px}
h2{font-size:22px;margin:34px 0 10px}h3{font-size:18px;margin:26px 0 8px}h4{font-size:16px;margin:20px 0 6px}
p,li{color:#232a36}
a{color:var(--accent)}
code{background:var(--code);padding:1px 5px;border-radius:4px;font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow:auto}
pre code{background:none;padding:0;font-size:13px;line-height:1.55}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14.5px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#fafbfd}
img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}
aside{border-left:3px solid #f7b500;background:#fffdf3;padding:10px 14px;margin:16px 0;border-radius:0 6px 6px 0}
blockquote{border-left:3px solid var(--line);margin:16px 0;padding:4px 16px;color:var(--mut)}
footer{border-top:1px solid var(--line);padding:16px 34px;color:var(--mut);font-size:12.5px}
#q{flex:1 1 260px;max-width:340px;padding:7px 11px;border:1px solid var(--line);border-radius:7px;font-size:14px}
.hit{display:block;padding:9px 11px;border:1px solid var(--line);border-radius:7px;margin:0 0 8px;text-decoration:none;color:var(--ink)}
.hit:hover{border-color:#c9d4ff;background:#f7f9ff}
.hit .t{font-weight:600}.hit .s{color:var(--mut);font-size:13px;margin-top:3px}
.hit mark{background:#fff2a8}
@media print{header,nav,#q,.hits{display:none}main{padding:0}.wrap{display:block}}
"""

OFFLINE_JS = """
const P=%PAGES%, TXT=%TEXT%;
const q=document.getElementById('q'), hits=document.getElementById('hits');
const main=document.getElementById('main'), nav=document.getElementById('nav');
function show(slug){
  const p=P[slug]; if(!p) return;
  main.innerHTML=p.h; document.title=p.t+' - Pine Script manual';
  for(const a of nav.querySelectorAll('a')) a.classList.toggle('on',a.dataset.s===slug);
  try{localStorage.setItem('pslast',slug)}catch(e){}
  window.scrollTo(0,0); main.focus();
}
function index(){
  if(!q.value.trim()){hits.innerHTML='';return}
  const terms=q.value.toLowerCase().split(/\\s+/).filter(Boolean);
  const out=[];
  for(const slug in TXT){
    const t=TXT[slug].toLowerCase(); let score=0;
    for(const term of terms){const at=t.indexOf(term); if(at<0){score=-1;break}
      score+=Math.max(0,12-Math.floor(at/400)); score+=(t.split(term).length-1)}
    if(score>0)out.push([score,slug]);
  }
  out.sort((a,b)=>b[0]-a[0]);
  hits.innerHTML=out.slice(0,25).map(([s,slug])=>
    `<a class="hit" href="#" onclick="show('${slug}');return false"><div class="t">${P[slug].t}</div>`+
    `<div class="s">${TXT[slug].slice(0,150).replace(/</g,'&lt;')}...</div></a>`).join('')||
    '<p style="color:#5b6472">No page contains all of those words.</p>';
}
q.addEventListener('input',index);
nav.addEventListener('click',e=>{const a=e.target.closest('a'); if(a){e.preventDefault();show(a.dataset.s)}});
hits.addEventListener('click',e=>{const a=e.target.closest('a'); if(a) e.preventDefault()});
const first=(()=>{try{return localStorage.getItem('pslast')}catch(e){return null}})()||%FIRST%;
show(first);
"""


def rewrite_srcs(frag: str, images: dict[str, Path], prefix: str = "assets/") -> str:
    """Point every <img> at its local copy, every other link at TradingView.
    `prefix` is where the copies will sit relative to the page that shows them.
    """
    by_name = {p.name: p for p in images.values()}

    def local(m: re.Match) -> str:
        src = m.group(1)
        p = images.get(src)
        if p is not None:
            return f'src="{prefix}{p.name}"'
        tail = by_name.get(Path(src).name)
        return f'src="{prefix}{tail.name}"' if tail else m.group(0)

    frag = re.sub(r'src="(/[^"]+)"', local, frag)
    frag = re.sub(r'href="(/pine-script-docs[^"]*)"',
                  lambda m: f'href="{ROOT}{m.group(1)}" target="_blank" rel="noopener"', frag)
    return frag


def prepare_images(version: str, manifest: dict, shrink: bool) -> dict[str, Path]:
    """Fetch each figure once, cached by URL hash.

    shrink=True is for the PDF: Chrome re-encodes every raster it embeds, and 827
    full-size webp figures produced a 300 MB PDF. An 880 px JPEG at quality 68
    keeps the figures legible at print size and puts the PDF back in the tens of
    megabytes. shrink=False keeps the original bytes for the offline bundle,
    where fidelity costs nothing but the download is already compressed.
    """
    pages = manifest["pages"]
    urls: set[str] = set()
    for p in pages:
        frag = (KB / version / "pages" / (p["slug"] + ".html")).read_text(encoding="utf-8")
        urls |= set(re.findall(r'<img[^>]+src="(/[^"]+)"', frag))
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for i, u in enumerate(sorted(urls), 1):
        raw = IMG_CACHE / (hashlib.sha256(u.encode()).hexdigest()[:16] + "-" + Path(u).name)
        if not raw.exists():
            req = urllib.request.Request(ROOT + u, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    raw.write_bytes(r.read())
            except Exception as e:                    # noqa: BLE001 - one figure
                print(f"    image {type(e).__name__}: {u[-50:]}", flush=True)
                continue
            time.sleep(0.12)
        if shrink:
            jpg = raw.with_suffix(".pdf.jpg")
            if not jpg.exists():
                subprocess.run(["sips", "-Z", "880", "-s", "format", "jpeg",
                                "-s", "formatOptions", "68", str(raw), "--out", str(jpg)],
                               capture_output=True)
            out[u] = jpg if jpg.exists() else raw
        else:
            out[u] = raw
        if i % 100 == 0:
            print(f"    figures {i}/{len(urls)}", flush=True)
    print(f"  figures: {len(out)}/{len(urls)} ready", flush=True)
    return out


def build_offline(version: str, manifest: dict) -> Path:
    pages = manifest["pages"]
    images = prepare_images(version, manifest, shrink=False)
    bundle: dict[str, dict] = {}
    texts: dict[str, str] = {}
    for p in pages:
        slug = p["slug"]
        frag = (KB / version / "pages" / (slug + ".html")).read_text(encoding="utf-8")
        frag = rewrite_srcs(frag, images)
        title = p["title"]
        bundle[slug] = {"t": title, "h": frag}
        # the search index wants prose, not a code dump: keep the first 6k
        texts[slug] = to_text(frag)[:6000]

    groups: dict[str, list[str]] = {}
    for p in pages:
        top = p["slug"].split("/")[0]
        groups.setdefault(top, []).append(p["slug"])
    nav_html = []
    for top, slugs in groups.items():
        nav_html.append(f'<div class="grp">{html.escape(top)}</div>')
        for s in slugs:
            nav_html.append(f'<a href="#" data-s="{html.escape(s)}">{html.escape(bundle[s]["t"])}</a>')

    stamp = manifest["scraped_at"][:10]
    label = "v6" if version == "v6" else version
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pine Script {label} manual - offline</title>
<style>{OFFLINE_CSS}</style></head>
<body>
<header>
  <b>Pine Script {label} manual, offline</b>
  <span class="meta">{len(pages)} pages, mirrored from TradingView {stamp}</span>
  <input id="q" placeholder="Search every page..." autocomplete="off">
</header>
<div class="wrap">
  <nav id="nav">{''.join(nav_html)}</nav>
  <main id="main" tabindex="-1"></main>
</div>
<footer>
  Source: TradingView Pine Script User Manual, https://www.tradingview.com/pine-script-docs/
  &mdash; content &copy; TradingView, Inc., mirrored for offline reading. Rebuild with refresh.py.
</footer>
<script>
{OFFLINE_JS.replace('%PAGES%', json.dumps(bundle, separators=(',', ':'))).replace('%TEXT%', json.dumps(texts, separators=(',', ':'))).replace('%FIRST%', json.dumps(pages[0]['slug']))}
</script>
</body></html>
"""
    DIST.mkdir(parents=True, exist_ok=True)
    single = DIST / f"pine-script-docs-{version}-offline.html"
    single.write_text(doc, encoding="utf-8")
    readme = (
        f"Pine Script {label} manual, mirrored for offline reading\n"
        f"=====================================================\n\n"
        f"Open {single.name} in any browser. No server, no internet needed.\n\n"
        f"Source: https://www.tradingview.com/pine-script-docs/\n"
        f"Content (c) TradingView, Inc. This mirror exists so members can read the\n"
        f"manual without a connection. It is not affiliated with or endorsed by\n"
        f"TradingView.\n\n"
        f"Pages: {len(pages)}\nFetched: {manifest['scraped_at']}\n"
        f"Rebuilt by refresh.py in the pinescriptdocscraper repo.\n"
    )
    zip_path = DIST / f"pine-script-docs-{version}-offline.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(f"pine-script-docs-{version}-offline/{single.name}", doc)
        z.writestr(f"pine-script-docs-{version}-offline/README.txt", readme)
        for name, path in {p.name: p for p in images.values()}.items():
            z.write(path, f"pine-script-docs-{version}-offline/assets/{name}")
        for p in pages:
            z.writestr(f"pine-script-docs-{version}-offline/pages/{p['slug']}.txt",
                       (KB / version / "pages" / (p['slug'] + ".txt")).read_text(encoding="utf-8"))
    print(f"  zip: {zip_path.name}  {zip_path.stat().st_size:,}B", flush=True)
    single.unlink(missing_ok=True)
    return zip_path


PDF_CSS = """@page{size:A4;margin:16mm 14mm}
body{font:10.5pt/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#1b1f2a}
h1{font-size:20pt;page-break-before:always;margin:0 0 10pt}
h1.first{page-break-before:avoid}
h2{font-size:14pt;margin:14pt 0 5pt}h3{font-size:12pt;margin:11pt 0 4pt}
code,pre{font:8.5pt/1.4 ui-monospace,Menlo,monospace}
pre{background:#f5f6f8;border:1px solid #e3e6ec;padding:6pt;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:9pt;margin:6pt 0}
th,td{border:1px solid #e3e6ec;padding:3pt 4pt;text-align:left}
img{max-width:100%}aside{background:#fffdf3;border-left:3px solid #f7b500;padding:6pt}
.cover{page-break-after:always}.cover h1{page-break-before:avoid;font-size:26pt}
.toc{font-size:10pt}.toc div{margin:2pt 0}
"""


def count_figures(version: str, manifest: dict) -> int:
    """How many distinct figures the manual carries. Counted, not downloaded."""
    urls: set[str] = set()
    for p in manifest["pages"]:
        frag = (KB / version / "pages" / (p["slug"] + ".html")).read_text(encoding="utf-8")
        urls |= set(re.findall(r'<img[^>]+src="([^"]+)"', frag))
    return len(urls)


def build_pdf(version: str, manifest: dict) -> Path | None:
    shell = next(iter(sorted(Path(os.path.expanduser("~/.cache/puppeteer/chrome-headless-shell"))
                             .glob("*/chrome-headless-shell-mac-*/chrome-headless-shell"))), None)
    if shell is None:
        print("  no chrome-headless-shell, PDF skipped", flush=True)
        return None
    pages = manifest["pages"]
    stamp = manifest["scraped_at"][:10]
    label = "v6" if version == "v6" else version
    # Figures are deliberately not embedded. 827 raster screenshots pushed the PDF
    # past 60 MB, which is a worse download than a PDF without them; the HTML
    # edition carries every one at full size. The cover says so.
    figures = count_figures(version, manifest)
    img_dir = None
    body = [f"""<div class="cover"><h1 class="first">Pine Script {label} User Manual</h1>
<p>Print edition, mirrored from TradingView on {stamp}.</p>
<p>{len(pages)} pages of text and code. The {figures} figures are not in this file;
the HTML edition has all of them. Source: https://www.tradingview.com/pine-script-docs/</p>
<p>Content &copy; TradingView, Inc. Not affiliated with or endorsed by TradingView.</p>
<h2>Contents</h2><div class="toc">"""]
    for i, p in enumerate(pages, 1):
        body.append(f'<div>{i}. {html.escape(p["title"])} <span style="color:#5b6472">'
                    f'({html.escape(p["slug"])})</span></div>')
    body.append("</div></div>")
    for i, p in enumerate(pages):
        frag = (KB / version / "pages" / (p["slug"] + ".html")).read_text(encoding="utf-8")
        frag = rewrite_srcs(frag, {}, prefix="")
        frag = re.sub(r"<img[^>]*>", "", frag)
        if i == 0:
            frag = re.sub(r"<h1", '<h1 class="first"', frag, count=1)
        body.append(f'<section data-slug="{html.escape(p["slug"])}">{frag}</section>')
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><style>{PDF_CSS}</style></head>'
           f'<body>{"".join(body)}</body></html>')
    tmp = DIST / f".{version}-pdf.html"
    DIST.mkdir(parents=True, exist_ok=True)
    tmp.write_text(doc, encoding="utf-8")
    out = DIST / f"pine-script-docs-{version}-manual.pdf"
    r = subprocess.run([str(shell), "--headless", "--disable-gpu", "--no-sandbox",
                        "--print-to-pdf-no-header", f"--print-to-pdf={out}",
                        "--virtual-time-budget=12000", tmp.as_uri()],
                       capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if img_dir is not None:
        shutil.rmtree(img_dir, ignore_errors=True)
    if not out.exists():
        print(f"  PDF failed: {r.stderr[-300:]}", flush=True)
        return None
    print(f"  pdf: {out.name}  {out.stat().st_size:,}B", flush=True)
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scrape", "build", "all"])
    ap.add_argument("--version", action="append", choices=sorted(VERSIONS))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    versions = a.version or ["v6", "v5"]

    if a.cmd in ("scrape", "all"):
        for v in versions:
            print(f"--- scraping {v} ---", flush=True)
            scrape(v, force=a.force)
    if a.cmd in ("build", "all"):
        for v in versions:
            mpath = KB / v / "manifest.json"
            if not mpath.exists():
                print(f"  {v}: no manifest, run scrape first", flush=True)
                continue
            manifest = json.loads(mpath.read_text())
            print(f"--- building {v} ({manifest['pages_ok']} pages) ---", flush=True)
            build_offline(v, manifest)
            build_pdf(v, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
