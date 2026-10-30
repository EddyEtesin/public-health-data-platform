from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    "dashboard/app.py",
    "src/health_data_platform/__init__.py",
    "dbt/dbt_project.yml",
    "dbt/profiles.yml",
    "README.md",
]


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_project_files_exist(relative_path: str) -> None:
    file_path = PROJECT_ROOT / relative_path

    assert file_path.is_file(), (
        f"Required project file is missing: {relative_path}"
    )