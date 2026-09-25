"""API clients for the services SchoolHub supports."""

from .drei_koeche import DreiKoecheClient
from .errors import SchoolhubApiError, SchoolhubAuthError, SchoolhubConnectionError
from .swop import SwopClient, normalize_url, school_name

__all__ = [
    "DreiKoecheClient",
    "SchoolhubApiError",
    "SchoolhubAuthError",
    "SchoolhubConnectionError",
    "SwopClient",
    "normalize_url",
    "school_name",
]
