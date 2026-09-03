from pathlib import Path

import pytest

from tools import deploy_pages


def make_deploy_tree(root: Path) -> Path:
    dist = root / "dist"
    for relative_path in deploy_pages.REQUIRED_FILES:
        path = dist / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    return dist


def test_validate_deploy_root_accepts_isolated_build(tmp_path):
    dist = make_deploy_tree(tmp_path)

    file_count, byte_count = deploy_pages.validate_deploy_root(dist)

    assert file_count == len(deploy_pages.REQUIRED_FILES)
    assert byte_count == 4 * len(deploy_pages.REQUIRED_FILES)


@pytest.mark.parametrize(
    "name", [".venv", ".venv-test", "venv", "env", "node_modules", ".cache"]
)
def test_validate_deploy_root_rejects_development_directories(tmp_path, name):
    dist = make_deploy_tree(tmp_path)
    forbidden = dist / name
    forbidden.mkdir()
    (forbidden / "artifact").write_text("test", encoding="utf-8")

    with pytest.raises(RuntimeError, match="forbidden development directory"):
        deploy_pages.validate_deploy_root(dist)


def test_validate_deploy_root_rejects_symlinks(tmp_path):
    dist = make_deploy_tree(tmp_path)
    (dist / "outside-link").symlink_to(tmp_path)

    with pytest.raises(RuntimeError, match="symlink in deploy tree"):
        deploy_pages.validate_deploy_root(dist)


def test_build_deploy_command_pins_pages_target(tmp_path, monkeypatch):
    dist = make_deploy_tree(tmp_path)
    wrangler = tmp_path / "node_modules" / ".bin" / "wrangler"
    monkeypatch.setattr(deploy_pages, "WRANGLER", wrangler)

    command = deploy_pages.build_deploy_command(dist)

    assert command == [
        str(wrangler),
        "pages",
        "deploy",
        str(dist.resolve()),
        "--project-name",
        "speechmap",
        "--branch",
        "main",
    ]
