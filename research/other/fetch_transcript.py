Retrieve a YouTube transcript using Supadata.

HOW TO RUN:
    python fetch_transcript.py <YouTube URL>

EXAMPLE:
    python fetch_transcript.py https://www.youtube.com/watch?v=dQw4w9WgXcQ

REQUIREMENTS:
    pip install requests

You need a free Supadata API key from: https://dash.supadata.ai/organizations/api-key
Set it as an environment variable called SUPADATA_API_KEY, or paste it directly
into the SUPADATA_API_KEY variable below (not recommended for shared projects).
"""

# ── Standard library imports ──────────────────────────────────────────────────
import sys          # Lets us read command-line arguments (the YouTube URL)
import os           # Lets us read environment variables (your API key)
import re           # Regular expressions — used to sanitise the filename
import time         # Used to wait between polling attempts for long videos
from datetime import datetime  # Used to timestamp the saved file
from urllib.parse import urlparse, parse_qs, urlencode  # URL helpers

# ── Third-party imports ───────────────────────────────────────────────────────
import requests     # The library we use to call the Supadata REST API
                    # Install with:  pip install requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit these if you prefer not to use environment variables
# ─────────────────────────────────────────────────────────────────────────────

# Your Supadata API key.
# Best practice: set it as an environment variable so it never appears in code.
#   macOS / Linux:  export SUPADATA_API_KEY="your_key_here"
#   Windows CMD:    set SUPADATA_API_KEY=your_key_here
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "YOUR_API_KEY_HERE")

# Where to save the transcript files (relative to where you run the script)
OUTPUT_DIR = os.path.join("research", "youtube-transcripts")

# Supadata API base URL
BASE_URL = "https://api.supadata.ai/v1"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Extract the YouTube video ID from the URL
# ─────────────────────────────────────────────────────────────────────────────
def extract_video_id(youtube_url: str) -> str | None:
    """
    Pull the video ID out of any common YouTube URL format.

    Why bother? Because YouTube URLs come in several shapes:
        https://www.youtube.com/watch?v=dQw4w9WgXcQ   ← standard
        https://youtu.be/dQw4w9WgXcQ                  ← short link
        https://youtube.com/shorts/dQw4w9WgXcQ         ← Shorts

    We need just the ID part (e.g. 'dQw4w9WgXcQ') to use in the API call
    and to build a sensible filename.
    """
    parsed = urlparse(youtube_url)

    # Short URLs: youtu.be/<ID>
    if parsed.netloc in ("youtu.be",):
        return parsed.path.lstrip("/").split("?")[0]

    # Standard and Shorts URLs: youtube.com/watch?v=<ID>  or  /shorts/<ID>
    if "youtube" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        # /shorts/<ID>
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1].split("?")[0]

    return None  # Unrecognised format


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Call the Supadata Transcript API
# ─────────────────────────────────────────────────────────────────────────────
def fetch_transcript(youtube_url: str) -> dict:
    """
    Send a GET request to Supadata's /v1/transcript endpoint.

    Key things happening here:
    - We pass the full YouTube URL as a query parameter called 'url'.
    - We set text=true so we get back a clean string instead of timestamped
      chunks (easier to read in a Markdown file).
    - We set mode=auto so Supadata tries to grab the existing caption track
      first, and only falls back to AI generation if none exists.
    - Authentication is done via the x-api-key request header.

    The API can respond in two ways:
        HTTP 200 → transcript is ready right now (most videos)
        HTTP 202 → video is large; Supadata gives us a jobId to poll
    """
    endpoint = f"{BASE_URL}/transcript"

    headers = {
        "x-api-key": SUPADATA_API_KEY,   # Authentication header
        "Content-Type": "application/json",
    }

    params = {
        "url":  youtube_url,  # The YouTube video URL
        "text": "true",       # Return plain text (not timestamped chunks)
        "lang": "en",         # Prefer English; falls back to video's language
        "mode": "auto",       # Try native captions first, then AI generation
    }

    print(f"\n🔍  Calling Supadata API for:\n    {youtube_url}\n")
    response = requests.get(endpoint, headers=headers, params=params, timeout=60)

    # Surface clear error messages for common problems
    if response.status_code == 401:
        raise SystemExit("❌  Authentication failed. Check your SUPADATA_API_KEY.")
    if response.status_code == 404:
        raise SystemExit("❌  Video not found or is private.")
    if response.status_code == 429:
        raise SystemExit("❌  Rate limit hit. Wait a moment and try again.")
    if response.status_code not in (200, 202):
        raise SystemExit(f"❌  Unexpected API response {response.status_code}:\n{response.text}")

    return response.json(), response.status_code


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Poll for async results (only needed for very long videos)
# ─────────────────────────────────────────────────────────────────────────────
def poll_for_result(job_id: str, max_attempts: int = 20, wait_seconds: int = 5) -> dict:
    """
    For large videos the API returns HTTP 202 with a jobId.
    We keep asking "is it done yet?" every few seconds until it is.
    """
    endpoint = f"{BASE_URL}/transcript/{job_id}"
    headers  = {"x-api-key": SUPADATA_API_KEY}

    print(f"⏳  Transcript is processing (job {job_id}). Polling every {wait_seconds}s …")

    for attempt in range(1, max_attempts + 1):
        time.sleep(wait_seconds)
        resp = requests.get(endpoint, headers=headers, timeout=30)
        data = resp.json()

        status = data.get("status", "")
        print(f"    Attempt {attempt}/{max_attempts} — status: {status}")

        if status == "completed":
            return data
        if status == "failed":
            raise SystemExit(f"❌  Transcript job failed: {data.get('error', 'Unknown error')}")

    raise SystemExit("❌  Timed out waiting for transcript. Try again later.")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Build a clean filename from the video ID and today's date
# ─────────────────────────────────────────────────────────────────────────────
def make_filename(video_id: str) -> str:
    """
    Returns something like:  dQw4w9WgXcQ_2026-06-14.md
    Using the video ID keeps filenames unique even if you fetch the
    same video twice on different days.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_id  = re.sub(r"[^\w-]", "_", video_id)   # Replace special chars with _
    return f"{safe_id}_{date_str}.md"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Write the transcript to a Markdown file
# ─────────────────────────────────────────────────────────────────────────────
def save_as_markdown(content: str, video_url: str, video_id: str, lang: str) -> str:
    """
    Wraps the raw transcript text in a tidy Markdown document and
    saves it to  research/youtube-transcripts/<filename>.md
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)   # Create the folder if it doesn't exist

    filename  = make_filename(video_id)
    filepath  = os.path.join(OUTPUT_DIR, filename)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    markdown = f"""# YouTube Transcript

**Source URL:** {video_url}
**Video ID:** `{video_id}`
**Language:** `{lang}`
**Fetched at:** {timestamp}
**Tool:** Supadata Transcript API

---

## Transcript

{content}

---

*Saved by fetch_transcript.py using the [Supadata API](https://supadata.ai)*
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)

    return filepath


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — tie all steps together
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # ── Validate input ────────────────────────────────────────────────────────
    if len(sys.argv) < 2:
        print("Usage:  python fetch_transcript.py <YouTube URL>")
        print("Example: python fetch_transcript.py https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        sys.exit(1)

    youtube_url = sys.argv[1].strip()

    # ── Validate API key ──────────────────────────────────────────────────────
    if SUPADATA_API_KEY == "YOUR_API_KEY_HERE":
        raise SystemExit(
            "❌  No API key found.\n"
            "    Set the environment variable:  export SUPADATA_API_KEY='your_key'\n"
            "    Or paste your key into SUPADATA_API_KEY at the top of this script.\n"
            "    Get a free key at: https://dash.supadata.ai/organizations/api-key"
        )

    # ── Extract video ID ──────────────────────────────────────────────────────
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise SystemExit(
            f"❌  Could not extract a video ID from: {youtube_url}\n"
            "    Make sure it is a valid YouTube URL."
        )
    print(f"✅  Video ID detected: {video_id}")

    # ── Fetch transcript ──────────────────────────────────────────────────────
    data, status_code = fetch_transcript(youtube_url)

    # Handle async job (HTTP 202)
    if status_code == 202:
        job_id = data.get("jobId")
        if not job_id:
            raise SystemExit("❌  API returned 202 but no jobId in response.")
        data = poll_for_result(job_id)

    # ── Extract transcript text and language ──────────────────────────────────
    content = data.get("content", "")
    lang    = data.get("lang", "unknown")

    if not content:
        raise SystemExit("❌  API returned an empty transcript. The video may have no audio.")

    word_count = len(content.split())
    print(f"✅  Transcript received — {word_count:,} words, language: {lang}")

    # ── Save to Markdown ──────────────────────────────────────────────────────
    filepath = save_as_markdown(content, youtube_url, video_id, lang)
    print(f"\n✅  Transcript saved to:\n    {filepath}\n")


if __name__ == "__main__":
    main()
