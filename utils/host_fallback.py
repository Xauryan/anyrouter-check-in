"""连接失败后的 hosts 风格域名解析 fallback。"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx


def normalize_host_fallbacks(host_fallbacks: Mapping[str, str] | None) -> dict[str, str]:
	"""规范化 hosts fallback 配置，返回 {hostname: ip}。"""
	if not host_fallbacks:
		return {}

	normalized: dict[str, str] = {}
	for host, ip_address in host_fallbacks.items():
		if not isinstance(host, str) or not isinstance(ip_address, str):
			continue
		clean_host = _normalize_hostname(host)
		clean_ip = ip_address.strip()
		if clean_host and clean_ip:
			normalized[clean_host] = clean_ip
	return normalized


def get_url_host(url: str) -> str:
	return _normalize_hostname(urlparse(url).hostname or '')


def browser_host_resolver_args(host_fallbacks: Mapping[str, str] | None) -> list[str]:
	"""生成 Chromium 的 hosts 解析参数。"""
	normalized = normalize_host_fallbacks(host_fallbacks)
	if not normalized:
		return []

	rules = ','.join(f'MAP {host} {ip_address}' for host, ip_address in sorted(normalized.items()))
	return [f'--host-resolver-rules={rules}']


def is_http_connection_failure(exc: Exception) -> bool:
	return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def is_browser_connection_failure(exc: Exception) -> bool:
	message = str(exc).lower()
	connection_markers = (
		'err_name_not_resolved',
		'err_connection',
		'err_address_unreachable',
		'err_internet_disconnected',
		'err_tunnel_connection_failed',
		'net::',
		'name not resolved',
		'temporary failure in name resolution',
		'could not resolve host',
		'nx_domain',
		'timeout',
	)
	return any(marker in message for marker in connection_markers)


@contextmanager
def override_getaddrinfo(host_fallbacks: Mapping[str, str] | None):
	"""在当前同步请求期间把指定 hostname 解析到固定 IP。"""
	normalized = normalize_host_fallbacks(host_fallbacks)
	if not normalized:
		yield
		return

	original_getaddrinfo = socket.getaddrinfo

	def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
		if isinstance(host, str):
			ip_address = normalized.get(_normalize_hostname(host))
			if ip_address:
				host = ip_address
		return original_getaddrinfo(host, port, family, type, proto, flags)

	socket.getaddrinfo = patched_getaddrinfo
	try:
		yield
	finally:
		socket.getaddrinfo = original_getaddrinfo


class HostFallbackClient:
	"""httpx.Client 的轻量包装：连接失败后按 hosts fallback 重试。"""

	def __init__(
		self,
		client: httpx.Client,
		host_fallbacks: Mapping[str, str] | None,
		*,
		account_name: str = '',
	) -> None:
		self._client = client
		self._host_fallbacks = normalize_host_fallbacks(host_fallbacks)
		self._enabled_hosts: set[str] = set()
		self._account_name = account_name

	def get(self, url: str, **kwargs) -> httpx.Response:
		return self.request('GET', url, **kwargs)

	def post(self, url: str, **kwargs) -> httpx.Response:
		return self.request('POST', url, **kwargs)

	def request(self, method: str, url: str, **kwargs) -> httpx.Response:
		host = get_url_host(url)
		ip_address = self._host_fallbacks.get(host)
		if not ip_address:
			return self._client.request(method, url, **kwargs)

		if host in self._enabled_hosts:
			with override_getaddrinfo({host: ip_address}):
				return self._client.request(method, url, **kwargs)

		try:
			return self._client.request(method, url, **kwargs)
		except Exception as exc:
			if not is_http_connection_failure(exc):
				raise

			self._enabled_hosts.add(host)
			label = f'{self._account_name}: ' if self._account_name else ''
			print(
				f'[WARN] {label}Connection to {host} failed ({exc.__class__.__name__}); '
				f'retrying with hosts fallback {host} -> {ip_address}'
			)
			with override_getaddrinfo({host: ip_address}):
				return self._client.request(method, url, **kwargs)


def _normalize_hostname(host: str) -> str:
	value = host.strip().lower().rstrip('.')
	if '://' in value:
		value = urlparse(value).hostname or ''
	if ':' in value and not value.startswith('['):
		value = value.split(':', 1)[0]
	return value
