"""Errors the converter raises on purpose; the CLI prints their message and exits with status 2."""


class Rbr2StjError(Exception):
    """Base class: a problem with the input the user can act on."""


class FormatError(Rbr2StjError):
    """A file is truncated, malformed or not the format it claims to be."""


class ProtectedFileError(Rbr2StjError):
    """A file is protected (RSF-encrypted or obscured). rbr2stj never decrypts, so it refuses it."""
