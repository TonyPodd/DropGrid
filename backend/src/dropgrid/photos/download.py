"""SSRF checks at every redirect and at the actual TCP connection (DNS pinning)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from dropgrid.photos.domain import PhotoError, PhotoPolicy

Resolver = Callable[[str], Awaitable[list[str]]]


async def resolve_public(host: str) -> list[str]:
    addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(address[4][0]) for address in addresses})


async def public_addresses(host: str, resolver: Resolver) -> list[str]:
    try:
        addresses = await resolver(host)
        if not addresses or any(
            not ipaddress.ip_address(ip).is_global
            or ipaddress.ip_address(ip).is_multicast
            or ipaddress.ip_address(ip).is_reserved
            for ip in addresses
        ):
            raise PhotoError("download_destination_rejected")
        return addresses
    except (OSError, ValueError):
        raise PhotoError("download_destination_rejected") from None


def validate_url(url: str, policy: PhotoPolicy) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or host not in policy.trusted_hosts
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise PhotoError("download_destination_rejected")
        return host
    except ValueError:
        raise PhotoError("download_destination_rejected") from None


class PinnedPhotoTransport(httpx.AsyncBaseTransport):
    """TLS SNI/certificate validation retains original host, TCP uses checked IP.

    Pools are separated by original hostname, preventing cross-host TLS reuse.
    Environment proxies and redirects are deliberately disabled.
    """

    def __init__(self, policy: PhotoPolicy, resolver: Resolver = resolve_public) -> None:
        self.policy = policy
        self.resolver = resolver
        self.pools = {
            host: httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            for host in policy.trusted_hosts
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = validate_url(str(request.url), self.policy)
        addresses = await public_addresses(host, self.resolver)
        request.headers["host"] = host
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await self.pools[host].handle_async_request(pinned)

    async def aclose(self) -> None:
        for pool in self.pools.values():
            await pool.aclose()


class PhotoDownloader:
    def __init__(
        self, client: httpx.AsyncClient, policy: PhotoPolicy, resolver: Resolver = resolve_public
    ) -> None:
        self.client, self.policy, self.resolver = client, policy, resolver

    async def download(self, url: str) -> bytes:
        try:
            async with asyncio.timeout(self.policy.timeout_seconds):
                for redirect in range(self.policy.max_redirects + 1):
                    host = validate_url(url, self.policy)
                    await public_addresses(host, self.resolver)
                    async with self.client.stream("GET", url, follow_redirects=False) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if (
                                redirect == self.policy.max_redirects
                                or "location" not in response.headers
                            ):
                                raise PhotoError("download_redirect_rejected")
                            url = urljoin(url, response.headers["location"])
                            continue
                        if response.status_code != 200:
                            raise PhotoError("download_failed")
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise PhotoError("image_format_rejected")
                        if response.headers.get("content-type", "").split(";")[0].lower() not in {
                            "image/jpeg",
                            "image/png",
                            "image/webp",
                        }:
                            raise PhotoError("image_format_rejected")
                        length = response.headers.get("content-length")
                        if length and (
                            int(length) < 0 or int(length) > self.policy.max_input_bytes
                        ):
                            raise PhotoError("image_too_large")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if len(data) + len(chunk) > self.policy.max_input_bytes:
                                raise PhotoError("image_too_large")
                            data.extend(chunk)
                        return bytes(data)
        except (httpx.HTTPError, TimeoutError, ValueError, OSError):
            raise PhotoError("download_failed") from None
        raise PhotoError("download_failed")
