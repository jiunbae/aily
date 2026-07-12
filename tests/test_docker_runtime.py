"""Static regression checks for container runtime defaults."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_binds_to_container_interface():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert "ENV DASHBOARD_HOST=0.0.0.0" in dockerfile


def test_bridge_only_modes_do_not_require_dashboard_health_endpoint():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert "mode in ('dashboard', 'all')" in dockerfile
