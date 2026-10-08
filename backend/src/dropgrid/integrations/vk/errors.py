from typing import Any


class VKError(Exception):
    """Contains only local, fixed messages; no upstream body/request/cause."""

    def __init__(
        self, method: str, message: str, code: int | None = None, subcode: int | None = None
    ) -> None:
        self.method = method
        self.message = message
        self.code = code
        self.subcode = subcode
        super().__init__(f"{method}: {message}" + (f" (VK {code})" if code is not None else ""))

    def as_dict(self) -> dict[str, Any]:
        result = {"method": self.method, "message": self.message, "error_code": self.code}
        if self.subcode is not None:
            result["error_subcode"] = self.subcode
        return result


class VKAPIError(VKError):
    pass


class VKAuthenticationError(VKAPIError):
    pass


class VKRateLimitError(VKAPIError):
    pass


class VKPermissionError(VKAPIError):
    pass


class VKCommunityUnavailableError(VKPermissionError):
    pass


class VKCaptchaRequiredError(VKAPIError):
    pass


class VKTransportError(VKError):
    pass


class VKProtocolError(VKError):
    pass


class VKWriteDisabledError(VKError):
    pass


class VKCredentialUnavailableError(VKError):
    pass


class VKInputError(VKError):
    pass


def api_error(method: str, code: int) -> VKAPIError:
    if code in {5, 18, 27, 28}:
        return VKAuthenticationError(method, "Authorization failed", code)
    if code in {6, 9, 29}:
        return VKRateLimitError(method, "VK rate limit or flood control", code)
    if code in {14, 17}:
        return VKCaptchaRequiredError(method, "CAPTCHA or security validation required; stop", code)
    if code == 203 or (method in {"groups.getById", "wall.get"} and code == 15):
        return VKCommunityUnavailableError(method, "Community unavailable or access denied", code)
    if code in {7, 15, 20, 200, 201, 214}:
        return VKPermissionError(method, "Permission denied", code)
    return VKAPIError(method, "VK API request failed", code)
