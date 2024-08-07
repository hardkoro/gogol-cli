"""Custom exceptions."""


class GogolPinException(Exception):
    """Base exception."""


class InvalidEventURLError(GogolPinException):
    """Invalid event URL error."""


class EventNotFoundError(GogolPinException):
    """Event not found error."""
