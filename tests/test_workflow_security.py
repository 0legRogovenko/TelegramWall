"""Static security contracts for GitHub Actions workflows."""
import re
from pathlib import Path


WORKFLOW_DIR = Path(__file__).parents[1] / ".github" / "workflows"
WORKFLOWS = tuple(sorted(WORKFLOW_DIR.glob("*.yml")))
MUTABLE_ACTION = re.compile(r"uses:\s+[^\s]+@v\d+(?:\s|$)")


def test_workflows_pin_actions_to_commit_sha():
    violations = []
    for path in WORKFLOWS:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if MUTABLE_ACTION.search(line):
                violations.append(f"{path.name}:{number}: {line.strip()}")
    assert violations == []


def test_workflows_declare_read_only_contents_permission():
    for path in WORKFLOWS:
        text = path.read_text()
        assert re.search(
            r"^permissions:\n\s+contents:\s+read$", text, re.MULTILINE
        ), path


def test_ci_does_not_use_pull_request_target():
    text = (WORKFLOW_DIR / "ci.yml").read_text()
    assert "pull_request_target" not in text


def test_ci_lints_scripts_directory():
    text = (WORKFLOW_DIR / "ci.yml").read_text()
    assert "flake8 src tests main.py auth_userbot.py scripts" in text
