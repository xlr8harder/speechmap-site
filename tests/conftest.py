"""Shared fixtures: serve the generated site from the dist build root.

Tests run against generated HTML, so run
`uv run python preprocess.py --static-only` first if you've changed the
generators. Browser tests use pytest-playwright's `page` fixture
(one-time setup: `uv run playwright install chromium`).
"""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = REPO_ROOT / "dist"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def site_url():
    assert (BUILD_ROOT / "index.html").exists(), "no dist build; run preprocess.py first"
    handler = functools.partial(QuietHandler, directory=str(BUILD_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="session")
def a_theme_slug():
    """Slug of some generated theme page, without assuming a specific theme."""
    themes_dir = BUILD_ROOT / "themes"
    slugs = sorted(p.name for p in themes_dir.iterdir() if p.is_dir())
    assert slugs, "no generated theme pages; run preprocess.py first"
    return slugs[0]
