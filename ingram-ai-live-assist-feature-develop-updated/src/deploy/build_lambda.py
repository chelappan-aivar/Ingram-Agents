"""Build one Lambda zip for both functions (API and replayer), for Python 3.12 on arm64.

    python deploy/build_lambda.py [--out dist/functions.zip]

Dependencies are installed for manylinux aarch64, because the Transcribe SDK's
``awscrt`` is compiled code. Runs an import check against the staged files.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("app", "api", "replayer")
DATA = ("config/guidance",)
MAX_ZIP_BYTES = 50 * 1024 * 1024  # Lambda direct-upload limit.
PIP_TARGET = ["--platform", "manylinux2014_aarch64", "--implementation", "cp",
              "--python-version", "3.12", "--only-binary=:all:"]


def _pin(name: str, *files: str) -> str:
    for f in files:
        for line in (ROOT / f).read_text().splitlines():
            if line.strip().startswith(f"{name}=="):
                return line.strip()
    raise SystemExit(f"{name} is not pinned in {files}")


def build(out: Path) -> Path:
    # Bundled boto3: the Lambda runtime's copy may predate bedrock-agentcore.
    # pydantic: config profile validation in the API (app/config_schema.py).
    deps = [_pin("boto3", "requirements.txt"), _pin("pydantic", "requirements.txt"),
            _pin("amazon-transcribe", "requirements-dev.txt")]
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--target", str(staging), *PIP_TARGET, *deps], check=True)
        for package in PACKAGES:
            shutil.copytree(ROOT / package, staging / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for folder in DATA:
            shutil.copytree(ROOT / folder, staging / folder)

        # Import check: stdlib plus the staged files only (-I -S skips the venv).
        check = "import api.handler, replayer.lambda_handler; print('imports ok')"
        subprocess.run([sys.executable, "-I", "-S", "-c", f"import sys; sys.path.insert(0, '.'); {check}"],
                       cwd=staging, check=True)

        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    zf.write(path, path.relative_to(staging))

    size = out.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise SystemExit(f"{out} is {size // 1024 // 1024} MB, over Lambda's 50 MB direct-upload limit")
    print(f"Built {out} ({size // 1024} KB)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "functions.zip")
    build(parser.parse_args().out)


if __name__ == "__main__":
    main()
