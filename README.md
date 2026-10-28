# pinescriptdocscraper

Mirrors TradingView's Pine Script User Manual so it can be read offline.

Two entry points, on purpose:

- `scraper.py` is the original: crawl4ai + Playwright, BFS from the welcome page.
  It needs a 300 MB browser install and its depth limit drops pages that sit
  deeper than three clicks, so it is kept for reference rather than run.
- `refresh.py` is the pipeline that actually runs. The docs are server-rendered
  (Astro), so plain HTTP returns the same bytes, and the sidebar nav lists the
  whole manual. Standard library only.

## Run it

```sh
python3 refresh.py all --version v6     # fetch, then build both packages
python3 refresh.py scrape --version v6  # fetch only, into knowledge_base/v6/
python3 refresh.py build --version v6   # rebuild packages from the local mirror
```

Outputs land in `dist/`:

| file | what it is |
|---|---|
| `pine-script-docs-v6-offline.zip` | one searchable HTML file, every figure, the page text of all pages |
| `pine-script-docs-v6-manual.pdf` | the same pages set for print, text and code, no figures |

The PDF leaves the figures out on purpose: 827 embedded screenshots took it past
60 MB, which is a worse download than a PDF without them. The HTML edition has
all of them at full size.

## Where the output goes

The two packages are uploads, not repo contents. They are served by
pinescriptstrategy.com from R2 (`docs-offline/` prefix in the
`pinescriptstrategy-attachments` bucket) because a Workers static asset is capped
at 25 MiB per file and the zip is 37 MiB. Upload after a rebuild:

```sh
cd ~/hermes-projects/pinescriptstrategy/app
npx wrangler r2 object put "pinescriptstrategy-attachments/docs-offline/<file>" \
  --file ~/Projects/pinescript-docs-scraper/dist/<file> --remote \
  --content-type <application/zip|application/pdf>
```

Then update the byte count, page count and figure count in that repo's
`src/features/pine-script-resources/lib/offline-packages.ts`, which is what the
page renders and what the serving route allows.

## What is not committed

`knowledge_base/`, `dist/` and `.cache/` are gitignored. The mirror is TradingView's
content; the repo carries the procedure, and the packages live behind the site's
own domain with attribution rather than in a public GitHub history.

## Crawl manners

`refresh.py` sleeps between requests, caches every response by URL hash so a
rebuild never re-fetches, and treats a 404 as a missing page rather than an error.
`robots.txt` on the source allows `/pine-script-docs/`.
