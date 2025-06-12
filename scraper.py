import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy # Using Breadth-First Search
import os

# Define the starting point for our crawl and the pattern for the URLs we want to process.
START_URL = "https://www.tradingview.com/pine-script-docs/welcome/"
URL_PATTERN = "https://www.tradingview.com/pine-script-docs/"

# Define the output directory for our knowledge base.
OUTPUT_DIR = "knowledge_base"

async def main():
    """
    Main function to initialize and run the web crawler.
    """
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        print(f"Created directory: {OUTPUT_DIR}", flush=True)

    # Initialize the asynchronous web crawler.
    # Default BrowserConfig is usually fine (headless=True, JavaScript enabled by Playwright).
    crawler = AsyncWebCrawler()

    print(f"--- Starting deep crawl from {START_URL} ---", flush=True)

    # Configure the deep crawl strategy.
    # max_depth=3 means: start page (0) + links on it (1) + links on those (2) + links on those (3)
    # include_external=False keeps the crawler on the same domain.
    deep_crawl_strategy = BFSDeepCrawlStrategy(
        max_depth=3,
        include_external=False
    )

    # Configure the run settings for the crawler.
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=deep_crawl_strategy,
        # Wait for a specific element that indicates the navigation menu is loaded.
        # This selector targets a div whose class starts with 'documentation-nav-'
        wait_for_selector="div[class^='documentation-nav-']",
        # page_load_timeout=20000, # Optional: can add if wait_for_selector isn't enough (in ms)
        stream=True  # Process pages as they are crawled
    )

    # FIX: Initialize scraped_pages_data as an empty list
    scraped_pages_data = []
    pages_visited_count = 0

    # Perform the deep crawl. This will yield CrawlResult objects as pages are processed.
    async for page_data in crawler.adeep_crawl(url=START_URL, config=run_config):
        pages_visited_count += 1
        print(f"  - Visited ({pages_visited_count}): {page_data.url}", flush=True)

        if page_data.success and page_data.url.startswith(URL_PATTERN):
            print(f"    - Queuing for save: {page_data.url}", flush=True)
            scraped_pages_data.append(page_data)
        elif not page_data.success:
            print(f"    - Error crawling {page_data.url}: {page_data.error_message}", flush=True)
        else:
            print(f"    - Skipping (URL not matching pattern): {page_data.url}", flush=True)

    # --- Save the scraped content to files ---
    print(f"\n--- Processing and saving {len(scraped_pages_data)} relevant pages ---", flush=True)
    if not scraped_pages_data:
        print("No relevant pages were successfully scraped to be saved.", flush=True)
        return

    for page_data in scraped_pages_data:
        # Generate a more robust filename from the URL.
        path_part = page_data.url.split(URL_PATTERN)[-1] if URL_PATTERN in page_data.url else page_data.url.split('/')[-1]
        
        if not path_part or path_part.endswith('/'):
            file_name_base = (path_part.replace('/', '_').rstrip('_') if path_part else "")
            file_name = f"{file_name_base}_index.md" if file_name_base else "root_index.md"
        else:
            file_name = path_part.replace('/', '_').replace('.html', '.md')
        
        # Ensure filename is never empty
        if not file_name.strip() or file_name == ".md":
            file_name = page_data.url.split('/')[-2] + "_" + (page_data.url.split('/')[-1] or "index") + ".md"
            file_name = file_name.replace(".html","")


        file_path = os.path.join(OUTPUT_DIR, file_name)

        # Prefer 'fit_markdown' for cleaner content, fallback to 'raw_markdown'.
        markdown_to_save = None
        if page_data.markdown:
            if page_data.markdown.fit_markdown:
                markdown_to_save = page_data.markdown.fit_markdown
                print(f"    - Using fit_markdown for {page_data.url}", flush=True)
            elif page_data.markdown.raw_markdown:
                markdown_to_save = page_data.markdown.raw_markdown
                print(f"    - Using raw_markdown for {page_data.url}", flush=True)

        if markdown_to_save:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(markdown_to_save)
                print(f"  - Saved: {file_path}", flush=True)
            except Exception as e:
                print(f"  - Error saving {file_path}: {e}", flush=True)
        else:
            print(f"  - No markdown content (fit or raw) found for: {page_data.url}", flush=True)

    print(f"\nCrawl and processing complete! Visited {pages_visited_count} pages in total.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
