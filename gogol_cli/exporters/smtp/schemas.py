"""Schemas for SMTP Exporter."""

from pydantic import BaseModel


class SMTPConfig(BaseModel):
    """SMTP connection config."""

    host: str
    port: int
    username: str
    password: str


class EmailConfig(BaseModel):
    """Email config."""

    from_addr: str
    to_addr: str
    subject: str
