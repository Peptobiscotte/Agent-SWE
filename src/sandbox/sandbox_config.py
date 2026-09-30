# pyright: reportMissingTypeStubs = false
from pydantic import Field
from models_public import (
    SandboxConfig as _SandboxConfigBase,
)


class SandboxConfig(_SandboxConfigBase):
    """Sandbox configuration with moulinette-specific defaults."""
    authorized_imports: list[str] = Field(default_factory=lambda: [
        "math", "math.*",
        "collections", "collections.*",
        "itertools", "re", "json",
        "typing", "typing.*",
        "functools", "operator",
        "heapq", "bisect", "copy",
        "string", "random",
        "datetime", "datetime.*",
        "array", "cmath",
    ])
    allowed_directories: list[str] = Field(default_factory=lambda: [
        "/testbed", "/tmp/agent"
    ])
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512


class FinalAnswerException(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
