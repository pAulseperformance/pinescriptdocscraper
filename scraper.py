import asyncio
from crawl4ai import AsyncWebCrawler
import os

# Define the starting point for our crawl and the pattern for the URLs we want to follow.
# This ensures we only scrape the v6 documentation pages.
START_URL = "https://www.tradingview.com/pine-script-docs/welcome/"
URL_PATTERN = "https://www.tradingview.com/pine-script-docs/"

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

    # This list will hold the results from the crawl
    scraped_pages = []

    # Define a callback function to be executed for each crawled page.
    # This is where we process and save the content.
    async def on_page_crawled(page_data):
        # We only care about pages that match our documentation URL pattern.
        if page_data.url.startswith(URL_PATTERN):
            print(f"  - Scraped: {page_data.url}")
            # Add the scraped page data to our list
            scraped_pages.append(page_data)
            
    # Run the crawler.
    # We add `max_depth=5` to instruct the crawler to follow links.
    await crawler.arun(
        url=START_URL,
        on_page_crawled=on_page_crawled,
        allowed_path=URL_PATTERN,
        max_depth=5  # FIX: Tell the crawler to follow links up to 5 levels deep.
    )
    
    # After the crawl is complete, process the saved pages
    print("\n--- Processing and saving scraped pages ---")
    if not scraped_pages:
        print("No pages were scraped. Please check the URL and pattern.")
        return

    for page_data in scraped_pages:
        # Generate a clean filename from the URL.
        # Example: "https://.../language/Introduction.html" becomes "language_Introduction.md"
        # This logic handles nested paths now.
        relative_path = page_data.url.replace(URL_PATTERN, "").replace(".html", ".md")
        if not relative_path or relative_path.endswith('/'):
            relative_path += "index.md"
        file_name = relative_path.replace('/', '_') # Replace slashes to avoid creating subdirectories
            
        file_path = os.path.join(OUTPUT_DIR, file_name)

        # The page_data.markdown attribute contains the cleaned content.
        if page_data.markdown:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(page_data.markdown)
            print(f"  - Saved: {file_path}")
        else:
            print(f"  - No markdown content found for: {page_data.url}")


    print("\nCrawl and processing complete!")

if __name__ == "__main__":
    asyncio.run(main())
