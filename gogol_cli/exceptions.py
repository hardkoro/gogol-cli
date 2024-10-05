"""Custom exceptions."""


class GogolCLIException(Exception):
    """Base exception."""


class EmailConfigError(GogolCLIException):
    """Email config error."""


class EventNotFoundError(GogolCLIException):
    """Event not found error."""


class InvalidEventURLError(GogolCLIException):
    """Invalid event URL error."""


class SMTPConfigError(GogolCLIException):
    """SMTP config error."""
