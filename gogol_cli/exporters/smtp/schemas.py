"""Schemas for the SMTP exporter."""

from pydantic import BaseModel, ConfigDict


class SMTPConfig(BaseModel):
    """SMTP connection config."""

    model_config = ConfigDict(frozen=True)

    host: str
    port: int
    username: str
    password: str


class EmailConfig(BaseModel):
    """Email config."""

    model_config = ConfigDict(frozen=True)

    from_addr: str
    to_addr: str
    subject: str
