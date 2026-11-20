"""Errors whose message is safe to show to the person who uploaded the file.

Everything else is treated as an internal error: logged in full on the server, shown to the
user only as a generic message with a reference code.
"""


class UserInputError(ValueError):
    """The upload itself is the problem (unsupported, corrupt, too long...). Retrying won't help."""

    def __init__(self, message: str, code: str = "invalid_input"):
        super().__init__(message)
        self.code = code
        self.public_message = message
