"""Loads packaged static assets."""

from pathlib import Path

from app.modules.sow_generator.repositories.resource_loader import read_resource_bytes

_DEFAULT_LOGO = "ingram-micro-logo.png"


def load_company_logo(configured_path: str) -> bytes | None:
    """The configured logo (an absolute path to a file), else the packaged default logo."""
    configured = configured_path.strip()
    if configured:
        path = Path(configured)
        return path.read_bytes() if path.is_absolute() and path.is_file() else None
    return read_resource_bytes("assets", _DEFAULT_LOGO)
