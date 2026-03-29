"""Custom exceptions."""


class GogolCLIException(Exception):
    """Base exception."""


class EmailConfigError(GogolCLIException):
    """Email config error."""


class DBEventNotFoundError(GogolCLIException):
    """Database event not found error."""


class DBFileNotFoundError(GogolCLIException):
    """Database file not found error."""


class SSHNotConfiguredError(GogolCLIException):
    """Raised when an SSH file manager is required but was not provided."""


class InvalidEventURLError(GogolCLIException):
    """Invalid event URL error."""


class SMTPConfigError(GogolCLIException):
    """SMTP config error."""
