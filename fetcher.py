import feedparser
import requests
from bs4 import BeautifulSoup
import logging
from typing import List, Optional

# Local imports from the generated database module
from database import (
    get_connection,
    add_source,
    add_article,
    init_db,
)

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def clean_html(html: str) -> str:
    """Return plain text extracted from HTML.

    Very simple extraction: strip tags and concatenate the text of <p> elements.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Prefer paragraph text; fallback to all visible text
    paragraphs = soup.find_all("p")
    if paragraphs:
        text = "\n".join(p.get_text(separator=" ", strip=True) for p in paragraphs)
    else:
        text = soup.get_text(separator=" ", strip=True)
    # Collapse multiple whitespace
    return " ".join(text.split())



def fetch_article_text(url: str, timeout: int = 10) -> Optional[str]:
    """Download the article page and return cleaned text."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "NewsLensFetcher/1.0"}
        )
        resp.raise_for_status()

        resp.encoding = resp.apparent_encoding or resp.encoding

        article_text = clean_html(resp.text)

        if not article_text:
            logging.warning(f"No readable article text found: {url}")
            return None

        return article_text

    except requests.RequestException as e:
        logging.warning(f"Failed to fetch article {url}: {e}")
        return None

    except Exception as e:
        logging.warning(f"Failed to process article {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Core fetcher logic
# ---------------------------------------------------------------------------

def fetch_and_store_feed(source_id: int, feed_url: str) -> None:
    """Parse a single RSS/Atom feed and store new articles.

    * Duplicate URLs are ignored thanks to the UNIQUE constraint in the
      ``articles`` table.
    * Articles with very short bodies (<200 characters) are skipped – they
      provide little value for sentiment/loaded‑wording analysis.
    """
    logging.info(f"Fetching feed from {feed_url}")
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        logging.error(f"Error parsing feed {feed_url}: {feed.bozo_exception}")
        return

    for entry in feed.entries:
        # Basic metadata – RSS/Atom formats differ, so we fall back gracefully.
        url = entry.get("link") or entry.get("id")
        if not url:
            continue
        title = entry.get("title", "")
        published = entry.get("published") or entry.get("updated") or None
        # Some feeds already include a cleaned summary; otherwise we fetch the article.
        summary_html = entry.get("summary") or entry.get("description") or ""
        article_text = clean_html(summary_html) if summary_html else None

        if not article_text:
            article_text = fetch_article_text(url)
        if not article_text:
            # If we still have no text, skip this entry.
            logging.info(f"Skipping entry without extractable text: {url}")
            continue
        # Minimum length filter (configurable – here 80 chars)
        if len(article_text) < 80:
            logging.info(f"Skipping short article ({len(article_text)} chars): {url}")
            continue

        # Insert article; duplicates are ignored automatically.
        article_id = add_article(
            source_id=source_id,
            headline=title,
            url=url,
            publication_date=published,
            clean_text=article_text,
        )
        if article_id:
            logging.info(f"Stored article {article_id} from {url}")
        else:
            logging.debug(f"Article already exists (duplicate URL): {url}")


def fetch_all_feeds(min_interval_seconds: int = 0) -> None:
    """Iterate over enabled sources and fetch their feeds.

    ``min_interval_seconds`` can be used to throttle requests when running
    repeatedly (e.g., from a scheduler). For a one‑off run keep it ``0``.
    """
    init_db()  # Ensure tables exist before we query them.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT source_id, feed_url FROM sources WHERE enabled = 1")
    sources = cur.fetchall()
    conn.close()
    for row in sources:
        source_id = row["source_id"]
        feed_url = row["feed_url"]
        fetch_and_store_feed(source_id, feed_url)
        if min_interval_seconds:
            import time
            time.sleep(min_interval_seconds)

# ---------------------------------------------------------------------------
# Example usage (run as script)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Example: add a couple of demo sources if the table is empty.
    init_db()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sources")
    count = cur.fetchone()[0]
    conn.close()
    if count == 0:
        logging.info("No sources configured – adding example feeds.")
        add_source("BBC News", "http://feeds.bbci.co.uk/news/rss.xml")
        add_source("BBC Technology", "http://feeds.bbci.co.uk/news/technology/rss.xml")
        add_source("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml")
        add_source("NYT Top Stories", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml")
        add_source("The Guardian", "https://www.theguardian.com/world/rss")
    # Fetch all feeds and store new articles.
    fetch_all_feeds()
    logging.info("Fetching complete.")
