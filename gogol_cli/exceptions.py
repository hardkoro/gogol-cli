"""Custom exceptions."""


class GogolCLIException(Exception):
    """Base exception."""


class InvalidEventURLError(GogolCLIException):
    """Invalid event URL error."""


class EventNotFoundError(GogolCLIException):
    """Event not found error."""
