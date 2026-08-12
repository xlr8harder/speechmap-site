from pathlib import Path

import pytest

import preprocess


def test_build_root_is_isolated_and_contains_deployable_assets(tmp_path, monkeypatch):
    output = tmp_path / "dist"
    monkeypatch.setattr(preprocess, "BUILD_ROOT", output)

    preprocess.prepare_build_root(clean=True)

    assert Path.cwd() == output
    assert (output / ".speechmap-build-output").is_file()
    assert (output / "style.css").is_file()
    assert (output / "functions" / "themes" / "[theme]" / "m" / "[model].js").is_file()
    assert not (output / "preprocess.py").exists()


def test_build_root_refuses_source_repository(monkeypatch):
    monkeypatch.setattr(preprocess, "BUILD_ROOT", preprocess.REPO_ROOT)

    with pytest.raises(RuntimeError, match="unsafe build root"):
        preprocess.prepare_build_root(clean=True)
