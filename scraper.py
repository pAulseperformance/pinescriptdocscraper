import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
import os

# Define the starting point for our crawl and the pattern for the URLs we want to follow.
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

    crawler = AsyncWebCrawler()

    # --- STEP 1: Discover all documentation links from the start page ---
    print(f"--- Step 1: Discovering links from {START_URL} ---", flush=True)

    # FIX: Using a more specific selector for the navigation menu and forcing log flushing.
    run_config = CrawlerRunConfig(
        browser_config=BrowserConfig(
            headless=True,
            wait_for_selector="div[class^='documentation-nav-']" # More specific selector for the nav menu
        )
    )

    # Run the crawler on the single start URL with our new, robust config.
    start_page_data = await crawler.arun(
        url=START_URL,
        config=run_config
    )
    
    if not start_page_data or not start_page_data.links:
        print("Error: Could not retrieve links from the start page.", flush=True)
        # --- DEBUGGING: Print the start of the HTML to see what the crawler sees ---
        if start_page_data:
            print("--- Start of received HTML ---", flush=True)
            print(start_page_data.html[:500], flush=True)
            print("--- End of received HTML ---", flush=True)
        return

    # Filter the found links to only include documentation pages
    doc_links = {link for link in start_page_data.links if link.startswith(URL_PATTERN)}
    
    # --- LOGGING: Print discovered links for debugging ---
    print(f"\nFound {len(doc_links)} unique documentation links to scrape:", flush=True)
    for link in sorted(list(doc_links))[:15]: # Print first 15 for brevity
        print(f"  - {link}", flush=True)
    if len(doc_links) > 15:
        print(f"  - ... and {len(doc_links) - 15} more.", flush=True)

    if not doc_links:
        print("\nNo documentation links were found. Exiting.", flush=True)
        return

    # --- STEP 2: Scrape the content from each discovered link ---
    print("\n--- Step 2: Scraping content from each link ---", flush=True)
    scraped_pages = []

    async def on_page_crawled(page_data):
        print(f"  - Scraped: {page_data.url}", flush=True)
        scraped_pages.append(page_data)

    # Use arun_many to process our specific list of URLs
    await crawler.arun_many(
        urls=list(doc_links),
        on_page_crawled=on_page_crawled,
        config=run_config # Use the same robust config for all pages
    )

    # --- STEP 3: Save the scraped content to files ---
    print("\n--- Step 3: Processing and saving scraped pages ---", flush=True)
    if not scraped_pages:
        print("No pages were successfully scraped in the second step.", flush=True)
        return

    for page_data in scraped_pages:
        relative_path = page_data.url.replace(URL_PATTERN, "").replace(".html", ".md")
        if not relative_path or relative_path.endswith('/'):
            relative_path += "index.md"
        file_name = relative_path.replace('/', '_')
            
        file_path = os.path.join(OUTPUT_DIR, file_name)

        if page_data.markdown:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(page_data.markdown)
            print(f"  - Saved: {file_path}", flush=True)
        else:
            print(f"  - No markdown content found for: {page_data.url}", flush=True)

    print("\nCrawl and processing complete!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
