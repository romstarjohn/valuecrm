class ClickFunnelsError(Exception):
    """Base exception for ClickFunnels integration."""
    pass

class ClickFunnelsAPIError(ClickFunnelsError):
    """Raised when the ClickFunnels API returns an error."""
    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

class ClickFunnelsAuthError(ClickFunnelsAPIError):
    """Raised when authentication fails."""
    pass

class ClickFunnelsRateLimitError(ClickFunnelsAPIError):
    """Raised when rate limit is exceeded."""
    pass
