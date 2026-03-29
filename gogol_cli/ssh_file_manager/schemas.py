"""Schemas for SSH File Manager."""

from pydantic import BaseModel


class SSHConfig(BaseModel):
    """SSH connection config."""

    host: str
    username: str
    key_path: str
    base_path: str

    @property
    def is_valid(self) -> bool:
        """Validate that all required fields are set."""
        return all([self.host, self.username, self.key_path, self.base_path])
