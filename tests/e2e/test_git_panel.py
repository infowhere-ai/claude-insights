"""
Acceptance tests — Git Panel (CA-04).

Given the project has uncommitted file changes
When the dashboard loads
Then files are listed and the diff viewer works
"""

import subprocess
import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 5000


@pytest.fixture
def git_project(server: ServerContext):
    """A real git repo with one tracked modified file (pre-created project)."""
    name = "git-project"
    project = server.projects_root / name

    if not (project / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=str(project), check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(project))
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project))

        readme = project / "README.md"
        readme.write_text("# Initial\n")
        subprocess.run(["git", "add", "README.md"], cwd=str(project))
        subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"], cwd=str(project))

    (project / "README.md").write_text("# Modified\nNew content here.\n")
    (project / "new_feature.py").write_text("def hello(): pass\n")

    server.write_status(name, "idle")
    time.sleep(0.3)
    return name


def test_pending_files_listed(page: Page, server: ServerContext, git_project: str) -> None:
    """
    Given  the project has a modified file
    When   the dashboard loads with the project selected
    Then   #git-list shows README.md (git panel is open by default)
    """
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=git_project)

    expect(page.locator("#git-list")).to_contain_text("README.md", timeout=TIMEOUT)


def test_diff_viewer_opens_on_click(page: Page, server: ServerContext, git_project: str) -> None:
    """
    Given  README.md is listed in the git panel
    When   the user clicks the file
    Then   the diff viewer opens with the unified diff content
    """
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=git_project)

    expect(page.locator("#git-list")).to_contain_text("README.md", timeout=TIMEOUT)

    page.locator(".git-row").first.click()

    expect(page.locator("#diff-modal")).not_to_have_class("hidden", timeout=TIMEOUT)
    expect(page.locator("#diff-body")).to_contain_text("@@", timeout=TIMEOUT)
