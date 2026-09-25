"""Errors raised by the SchoolHub API clients."""


class SchoolhubApiError(Exception):
    """Base error: a service could not be used as expected."""


class SchoolhubConnectionError(SchoolhubApiError):
    """The service could not be reached."""


class SchoolhubAuthError(SchoolhubApiError):
    """The credentials were rejected."""
