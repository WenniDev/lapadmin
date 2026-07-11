import time

import gspread
from gspread.exceptions import APIError

is_ready = False
try:
    gc = gspread.service_account(filename="keys/google.json")
    is_ready = True
except FileNotFoundError as e:
    msg = f"File not found: {e}"
    msg += "\nImporting from Google Sheets will not work."
    print(msg)

    class gc:
        def open(self, *args, **kwargs):
            raise ValueError(msg)


def with_backoff(func, *args, max_retries=6, base_delay=10, **kwargs):
    """Call func, retrying with exponential backoff on quota errors (429)."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            if e.code != 429 or attempt == max_retries - 1:
                raise
            retry_after = e.response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else base_delay * (2**attempt)
            print(f"Quota exceeded, retrying in {delay:.0f}s...")
            time.sleep(delay)
