from pathlib import Path
import json
import os


class PathManager:

    def __init__(self, config_path="config/app_config.json"):
        candidate = Path(config_path)
        self.app_root = Path(__file__).resolve().parents[2]
        self.config_path = candidate if candidate.is_absolute() else self.app_root / candidate
        self.config = self._load_config()

    def _path(self, key):
        configured = Path(self.config["paths"][key])
        override = os.getenv("ITNI_AI_TEAM_ROOT")
        base = Path(override).expanduser() if override else self.app_root
        if configured.is_absolute():
            return configured
        if key == "root":
            return (base / configured).resolve()
        return (self.root / configured).resolve()

    def _load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @property
    def root(self):
        return self._path("root")

    @property
    def data(self):
        return self._path("data")

    @property
    def database(self):
        return self._path("database")

    @property
    def projects(self):
        return self._path("projects")

    @property
    def logs(self):
        return self._path("logs")

    def ensure_directories(self):
        directories = [
            self.root,
            self.data,
            self.database,
            self.projects,
            self.logs
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
