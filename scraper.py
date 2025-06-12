import asyncio
from crawl4ai import AsyncWebCrawler
import os

# Define the starting point for our crawl and the pattern for the URLs we want to follow.
# This ensures we only scrape the v6 documentation pages.
START_URL = "https://www.tradingview.com/pine-script-docs/en/v6/index.html"
URL_PATTERN = "https://www.tradingview.com/pine-script-docs/en/v6/"

# Define the output directory for our knowledge base.
OUTPUT_DIR = "knowledge_base"

async def main():
    """
    Main function to initialize and run the web crawler.
    """
    # Create the output directory if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        print(f"Created directory: {OUTPUT_DIR}")

    # Initialize the asynchronous web crawler
    crawler = AsyncWebCrawler()

    print(f"Starting crawl from: {START_URL}")

    # Define a callback function to be executed for each crawled page.
    # This is where we process and save the content.
    async def on_page_crawled(page_data):
        # We only care about pages that match our documentation URL pattern.
        if page_data.url.startswith(URL_PATTERN):
            print(f"  - Processing: {page_data.url}")

            # Generate a clean filename from the URL.
            # Example: "https://.../Introduction.html" becomes "Introduction.md"
            file_name = page_data.url.split('/')[-1].replace('.html', '.md')
            file_path = os.path.join(OUTPUT_DIR, file_name)

            # The page_data.markdown attribute contains the cleaned content.
            if page_data.markdown:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(page_data.markdown)
                print(f"    - Saved to: {file_path}")
            else:
                print(f"    - No markdown content found for this page.")

    # Run the crawler.
    # It will start at the START_URL and follow links that match the pattern.
    # The 'on_page_crawled' function will be called for each page.
    # FIX: The method was renamed from 'run' to 'arun' in a newer version of the library.
    await crawler.arun(
        url=START_URL,
        on_page_crawled=on_page_crawled,
        allowed_path=URL_PATTERN
    )

    print("Crawl complete!")

if __name__ == "__main__":
    asyncio.run(main())
