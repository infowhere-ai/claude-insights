"""
Acceptance tests — Git Panel (CA-04).

Dado que o projecto tem ficheiros com alterações não commitadas
Quando o dashboard carrega
Então os ficheiros aparecem listados e o diff viewer funciona
"""

import subprocess
import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 5000


@pytest.fixture
def git_project(server: ServerContext):
    """A real git repo with one tracked + one modified file (pre-created project)."""
    name = "git-project"
    project = server.projects_root / name

    # Clean any previous git state
    git_dir = project / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init", "-q"], cwd=str(project), check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(project))
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project))

        readme = project / "README.md"
        readme.write_text("# Initial\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(project))
        subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"], cwd=str(project))

    # Always ensure README.md is modified (reset in case of previous test state)
    (project / "README.md").write_text("# Modified\nNew content here.\n")
    (project / "new_feature.py").write_text("def hello(): pass\n")

    # Reset server-side status
    server.write_status(name, "idle")
    time.sleep(0.3)
    return name


def test_pending_files_listed(page: Page, server: ServerContext, git_project: str) -> None:
    """
    Dado que   o projecto tem um ficheiro modificado
    Quando     o dashboard carrega com o projecto seleccionado
    Então      #git-list mostra README.md (btn-git está active/aberto por defeito)
    """
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=git_project)

    # git panel is already open (btn-git is active by default) — no click needed
    expect(page.locator("#git-list")).to_contain_text("README.md", timeout=TIMEOUT)


def test_diff_viewer_opens_on_click(page: Page, server: ServerContext, git_project: str) -> None:
    """
    Dado que   README.md está listado no painel git
    Quando     o utilizador clica no ficheiro
    Então      o diff viewer abre com conteúdo do diff
    """
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=git_project)

    expect(page.locator("#git-list")).to_contain_text("README.md", timeout=TIMEOUT)

    # Click the first git row (README.md)
    page.locator(".git-row").first.click()

    expect(page.locator("#diff-modal")).not_to_have_class("hidden", timeout=TIMEOUT)
    expect(page.locator("#diff-body")).to_contain_text("@@", timeout=TIMEOUT)
