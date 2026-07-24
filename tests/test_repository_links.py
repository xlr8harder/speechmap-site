from preprocess import render_resources_page


def test_resources_page_links_split_repositories() -> None:
    html = render_resources_page()

    assert "https://github.com/xlr8harder/speechmap-data" in html
    assert "https://github.com/xlr8harder/speechmap-eval" in html
    assert "https://github.com/xlr8harder/speechmap-site" in html
    assert "https://github.com/xlr8harder/llm-compliance" not in html
