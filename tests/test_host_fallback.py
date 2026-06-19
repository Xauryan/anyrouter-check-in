import socket

import httpx

from utils.host_fallback import HostFallbackClient, browser_host_resolver_args, normalize_host_fallbacks


def test_normalize_host_fallbacks_accepts_urls_and_hosts():
	assert normalize_host_fallbacks(
		{
			'https://AnyRouter.Top:443': '47.246.23.192',
			'custom.example.com.': '192.0.2.1',
			'': '192.0.2.2',
		}
	) == {
		'anyrouter.top': '47.246.23.192',
		'custom.example.com': '192.0.2.1',
	}


def test_browser_host_resolver_args():
	assert browser_host_resolver_args({'anyrouter.top': '47.246.23.192'}) == [
		'--host-resolver-rules=MAP anyrouter.top 47.246.23.192'
	]


def test_host_fallback_client_retries_connect_error_with_fixed_ip(monkeypatch):
	calls = []

	class FakeClient:
		def request(self, method, url, **kwargs):
			resolved = socket.getaddrinfo('anyrouter.top', 443)[0][4][0]
			calls.append((method, url, resolved, kwargs))
			if len(calls) == 1:
				raise httpx.ConnectError('Name or service not known')
			return httpx.Response(200, json={'success': True})

	def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
		return [(socket.AF_INET, socket.SOCK_STREAM, proto, '', (host, port))]

	monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

	client = HostFallbackClient(FakeClient(), {'anyrouter.top': '47.246.23.192'}, account_name='Account 1')

	response = client.get('https://anyrouter.top/api/user/self', headers={'Accept': 'application/json'})

	assert response.status_code == 200
	assert calls[0][2] == 'anyrouter.top'
	assert calls[1][2] == '47.246.23.192'


def test_host_fallback_client_reuses_fixed_ip_after_fallback(monkeypatch):
	resolved_hosts = []

	class FakeClient:
		def __init__(self):
			self.request_count = 0

		def request(self, method, url, **kwargs):
			self.request_count += 1
			resolved_hosts.append(socket.getaddrinfo('anyrouter.top', 443)[0][4][0])
			if self.request_count == 1:
				raise httpx.ConnectError('Name or service not known')
			return httpx.Response(200)

	def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
		return [(socket.AF_INET, socket.SOCK_STREAM, proto, '', (host, port))]

	monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

	client = HostFallbackClient(FakeClient(), {'anyrouter.top': '47.246.23.192'})

	assert client.get('https://anyrouter.top/api/user/self').status_code == 200
	assert client.post('https://anyrouter.top/api/user/sign_in').status_code == 200
	assert resolved_hosts == ['anyrouter.top', '47.246.23.192', '47.246.23.192']
