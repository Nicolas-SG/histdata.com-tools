"""Load the mirror configuration from a TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from hdmirror import months

DEFAULT_CONFIG_PATH = Path("~/.config/hdmirror/mirror.toml").expanduser()
DEFAULT_PAIRS = [
    "GBPUSD", "EURUSD", "USDCHF", "USDCAD", "XAUUSD",
    "AUDUSD", "EURAUD", "EURNZD", "GBPAUD", "GBPNZD", "NZDUSD", "XAGUSD",
]


@dataclass
class Config:
    """Mirror settings. Every field can be set in the TOML file."""

    root: Path = Path("/media/storage/Vegas_Files/Data bases/Hist Data")
    parquet_root: Path | None = None
    pairs: list[str] = field(default_factory=lambda: list(DEFAULT_PAIRS))
    start: str = "200801"
    delay_seconds: float = 4.0
    retries: int = 3
    timeout: float = 60.0
    recheck_months: int = 2
    absent_recheck_days: int = 90
    notify: bool = True
    log_file: Path = Path("~/.local/state/hdmirror/hdmirror.log")

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        self.parquet_root = (
            Path(self.parquet_root).expanduser()
            if self.parquet_root
            else self.root / "_parquet"
        )
        self.log_file = Path(self.log_file).expanduser()
        self.pairs = [pair.upper() for pair in self.pairs]
        months.check(self.start)

    @property
    def state_file(self) -> Path:
        return self.root / ".mirror_state.json"

    @property
    def lock_file(self) -> Path:
        return self.root / ".mirror.lock"

    @property
    def quarantine_dir(self) -> Path:
        return self.root / "_quarantine"


def load(path: Path | None = None) -> Config:
    """Read ``path`` (or the default config file if it exists)."""
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        if path != DEFAULT_CONFIG_PATH:
            raise FileNotFoundError(path)
        return Config()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")
    return Config(**data)
