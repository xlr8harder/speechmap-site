"""Lexical Fingerprints (experiments/vocab) page tests.

Static checks against generated HTML plus browser checks for the word view,
model-page word checker, and dot interactivity. Requires a dist build that
includes the vocab section.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
VOCAB = DIST / "experiments" / "vocab"

pytestmark = pytest.mark.skipif(
    not VOCAB.exists(), reason="dist has no vocab build (run preprocess.py)")


# ---- static structure

def test_experiments_index_has_card_and_boilerplate():
    html = (DIST / "experiments" / "index.html").read_text()
    assert "Lexical Fingerprints" in html
    assert "context-note" in html          # standing WHY_WE_TEST boilerplate
    assert "page-intro" in html


def test_overview_page_structure():
    html = (VOCAB / "index.html").read_text()
    for marker in ("wordsearch", "overview-content", "drift-grid",
                   "Lab drift over time", "closest",
                   "Rising across models", "context-note", "vocab.js"):
        assert marker in html, marker


def test_model_pages_exist_with_signature_and_quotes():
    models = json.loads((DIST / "data" / "vocab" / "models.json").read_text())
    assert len(models) > 300
    sample = models[-1]
    page = VOCAB / "m" / sample["slug"] / "index.html"
    assert page.exists()
    html = page.read_text()
    assert "Signature words" in html
    assert "modelwordsearch" in html
    assert "Sounds like" in html


def test_word_shards_complete_only_universal_pointers():
    wdir = DIST / "data" / "vocab" / "w"
    shard = json.loads(next(iter(sorted(wdir.glob("*.json")))).read_text())
    entry = next(iter(shard.values()))
    assert all(len(row) == 3 for row in entry["m"])   # [model, rate, first-use qid]


def test_theme_shard_cards_have_variation_anchors():
    shards = sorted((DIST / "data" / "theme-shards").glob("*.0.json"))
    entry = next(iter(json.loads(shards[0].read_text()).values()))
    assert 'id="v' in entry["html"]


# ---- browser behavior (pytest-playwright `page` + conftest site server)

def test_word_view_renders_with_zero_rail(page, site_url):
    page.goto(f"{site_url}/experiments/vocab/?w=ordinarily")
    page.wait_for_selector("#wordview svg", timeout=10000)
    dots = page.eval_on_selector_all("#wordview .wdot", "els => els.length")
    hollow = page.eval_on_selector_all("#wordview .wdot[stroke]", "els => els.length")
    assert dots > 300
    assert 0 < hollow < dots
    assert page.eval_on_selector("#overview-content", "e => e.hidden")


def test_word_search_autocomplete_prefix(page, site_url):
    page.goto(f"{site_url}/experiments/vocab/")
    page.focus("#wordsearch")
    page.wait_for_timeout(500)
    page.type("#wordsearch", "ordin")
    page.wait_for_timeout(400)
    options = page.eval_on_selector_all("#vocablist option", "els => els.map(e => e.value)")
    assert "ordinarily" in options


def test_model_checker_deeplink_extracts_snippet(page, site_url):
    models = json.loads((DIST / "data" / "vocab" / "models.json").read_text())
    sol = next(m for m in models if m["slug"] == "openai-gpt-5-6-sol")
    page.goto(f"{site_url}/experiments/vocab/m/{sol['slug']}/?w=ordinarily")
    page.wait_for_selector("#modelwordview .panel blockquote mark", timeout=15000)
    text = page.eval_on_selector("#modelwordview", "e => e.textContent")
    assert "/M" in text and "rank" in text


def test_first_uses_panel_for_rare_word(page, site_url):
    page.goto(f"{site_url}/experiments/vocab/?w=voss")
    page.wait_for_selector(".fu-rows .trend-row", timeout=10000)
    rows = page.eval_on_selector_all(".fu-rows .trend-row", "els => els.length")
    assert rows >= 3
