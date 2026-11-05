"""Process settings. Secrets stay in the environment and never enter a sandbox."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root, or TRUSTSCOUT_ROOT when set."""
    override = os.environ.get("TRUSTSCOUT_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    """Control-plane configuration."""

    root: Path
    data_dir: Path
    host_data_dir: Path
    public_base: str
    internal_base: str
    app_network: str
    runner: str
    code_image: str
    browser_image: str
    proxy_image: str
    eager: bool

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the environment."""
        root = repo_root()
        data_dir = Path(os.environ.get("TRUSTSCOUT_DATA_DIR", str(root / "data"))).resolve()
        host_raw = os.environ.get("TRUSTSCOUT_HOST_DATA_DIR", "").strip()
        host_data_dir = Path(host_raw).resolve() if host_raw else data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            data_dir=data_dir,
            host_data_dir=host_data_dir,
            public_base=os.environ.get("TRUSTSCOUT_PUBLIC_BASE", "http://localhost:8080").rstrip("/"),
            internal_base=os.environ.get(
                "TRUSTSCOUT_INTERNAL_BASE",
                "http://host.docker.internal:8080",
            ).rstrip("/"),
            app_network=os.environ.get("TRUSTSCOUT_APP_NETWORK", "").strip(),
            runner=os.environ.get("TRUSTSCOUT_RUNNER", "docker").strip().lower(),
            code_image=os.environ.get("TRUSTSCOUT_CODE_IMAGE", "trustscout-code"),
            browser_image=os.environ.get("TRUSTSCOUT_BROWSER_IMAGE", "trustscout-browser"),
            proxy_image=os.environ.get("TRUSTSCOUT_PROXY_IMAGE", "trustscout-egress"),
            eager=os.environ.get("TRUSTSCOUT_EAGER", "0") == "1",
        )

    def docker_path(self, path: Path) -> Path:
        """Map a file under data_dir to the path the Docker daemon can mount.

        Compose uses one absolute path for both, so this is usually identity.
        """
        try:
            relative = path.resolve().relative_to(self.data_dir.resolve())
        except ValueError:
            return path
        return self.host_data_dir / relative
