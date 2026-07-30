from types import SimpleNamespace
from typing import Any, cast

import pytest

import checkin
from utils.browser import _fetch_user_profile, verify_browser_login


class FakePage:
	def __init__(self, context, *, url='https://example.com/login', evaluate_results=None):
		self.context = context
		self.url = url
		self.evaluate_results = list(evaluate_results or [])
		self.listeners = {}
		self.reload_count = 0

	def on(self, event, listener):
		self.listeners[event] = listener

	def remove_listener(self, event, listener):
		if self.listeners.get(event) is listener:
			del self.listeners[event]

	async def goto(self, url, **kwargs):
		self.url = url

	async def reload(self, **kwargs):
		self.reload_count += 1

	async def wait_for_load_state(self, state, **kwargs):
		return None

	async def evaluate(self, expression, *args):
		return self.evaluate_results.pop(0)


class FakeContext:
	def __init__(self):
		self.page = FakePage(self)
		self.cleared = False
		self.closed = False

	async def new_page(self):
		return self.page

	async def clear_cookies(self):
		self.cleared = True

	async def cookies(self):
		return [{'name': 'session', 'value': 'test-session'}]

	async def close(self):
		self.closed = True


@pytest.mark.asyncio
async def test_fetch_user_profile_returns_profile_and_http_status():
	page = FakePage(
		None,
		evaluate_results=[
			{
				'status': 200,
				'payload': {'success': True, 'data': {'id': 42, 'username': 'test'}},
			}
		],
	)

	profile, status = await _fetch_user_profile(cast(Any, page))

	assert status == 200
	assert profile == {'id': 42, 'username': 'test'}


@pytest.mark.asyncio
async def test_verify_browser_login_reloads_console_then_retries_user_info(monkeypatch):
	page = FakePage(
		None,
		evaluate_results=[
			{'status': 503, 'payload': {'success': False}},
			{'status': 200, 'payload': {'success': True, 'data': {'id': 42}}},
		],
	)
	monkeypatch.setenv('CHECKIN_USER_INFO_MAX_ATTEMPTS', '2')
	monkeypatch.setenv('CHECKIN_USER_INFO_RETRY_DELAY_SECONDS', '0')

	profile = await verify_browser_login(cast(Any, page), 'https://example.com/console', timeout_ms=1)

	assert profile == {'id': 42}
	assert page.reload_count == 1
	assert page.listeners == {}


@pytest.mark.asyncio
async def test_login_with_credentials_retries_full_login_after_missing_session(monkeypatch, tmp_path):
	contexts = [FakeContext(), FakeContext()]
	launch_settings = []
	login_results = iter([False, True])
	login_calls = 0

	async def fake_launch_login_context(settings, **kwargs):
		launch_settings.append(settings)
		return contexts[len(launch_settings) - 1]

	async def fake_noop(*args, **kwargs):
		return None

	async def fake_false(*args, **kwargs):
		return False

	async def fake_login_with_email_form(*args, **kwargs):
		nonlocal login_calls
		login_calls += 1
		return next(login_results)

	async def fake_verify_browser_login(*args, **kwargs):
		return {'id': 42}

	provider = SimpleNamespace(
		domain='https://example.com',
		login_path='/login',
		persist_profile=True,
		use_proxy=False,
		host_fallbacks={},
	)
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	monkeypatch.setenv('CHECKIN_LOGIN_MAX_ATTEMPTS', '2')
	monkeypatch.setenv('CHECKIN_LOGIN_RETRY_DELAY_SECONDS', '0')
	monkeypatch.setattr(checkin, 'launch_login_context', fake_launch_login_context)
	monkeypatch.setattr(checkin, 'prepare_browser_page', fake_noop)
	monkeypatch.setattr(checkin, 'navigate_login_page', fake_noop)
	monkeypatch.setattr(checkin, 'is_logged_in', fake_false)
	monkeypatch.setattr(checkin, 'has_session_cookie', fake_false)
	monkeypatch.setattr(checkin, 'save_login_screenshot', fake_noop)
	monkeypatch.setattr(checkin, 'login_with_email_form', fake_login_with_email_form)
	monkeypatch.setattr(checkin, 'verify_browser_login', fake_verify_browser_login)

	result = await checkin.login_with_credentials(
		'可修改显示名',
		provider,
		'anyrouter',
		'user@example.com',
		'password',
	)

	assert result is not None
	assert result.cookies == {'session': 'test-session'}
	assert result.api_user == '42'
	assert login_calls == 2
	assert contexts[0].cleared is False
	assert contexts[1].cleared is True
	assert all(context.closed for context in contexts)
	assert launch_settings[0].profile_dir == launch_settings[1].profile_dir
	assert 'user@example.com' not in str(launch_settings[0].profile_dir)
