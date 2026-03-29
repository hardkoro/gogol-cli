"""Package with a service to manager files via SSH."""

from .file_manager import SSHFileManager
from .schemas import SSHConfig

__all__ = [
    "SSHConfig",
    "SSHFileManager",
]
