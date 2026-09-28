"""Schemas for SSH File Manager."""

import os

from pydantic import BaseModel, field_validator


class SSHConfig(BaseModel):
    """SSH connection config."""

    host: str
    username: str
    key_path: str
    base_path: str

    @field_validator("key_path")
    @classmethod
    def _expand_key_path(cls, value: str) -> str:
        """Expand `~` in the key path, since asyncssh treats it as a literal filesystem path."""
        return os.path.expanduser(value)

    @property
    def is_valid(self) -> bool:
        """Validate that all required fields are set."""
        return all([self.host, self.username, self.key_path, self.base_path])
