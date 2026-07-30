import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from utils.browser import build_browser_profile_key, launch_login_context, load_browser_login_settings


def test_browser_login_settings_records_profile_persistence(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)

	assert settings.persist_profile is False
	assert settings.profile_dir == tmp_path / 'agentrouter' / 'Account 1'


def test_browser_profile_key_is_stable_private_and_provider_scoped():
	key = build_browser_profile_key('AnyRouter', ' User@Example.com ')

	assert key == build_browser_profile_key('anyrouter', 'user@example.com')
	assert key != build_browser_profile_key('agentrouter', 'user@example.com')
	assert key != build_browser_profile_key('anyrouter', 'another@example.com')
	assert key.startswith('account-')
	assert 'user' not in key
	assert '@' not in key


def test_browser_login_settings_migrates_legacy_named_profile(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	legacy_profile = tmp_path / 'anyrouter' / '旧显示名'
	legacy_profile.mkdir(parents=True)
	(legacy_profile / 'state.txt').write_text('existing-session', encoding='utf-8')
	profile_key = build_browser_profile_key('anyrouter', 'user@example.com')

	settings = load_browser_login_settings(
		'旧显示名',
		'anyrouter',
		persist_profile=True,
		profile_key=profile_key,
	)

	assert settings.profile_dir == tmp_path / 'anyrouter' / profile_key
	assert (settings.profile_dir / 'state.txt').read_text(encoding='utf-8') == 'existing-session'
	assert legacy_profile.is_dir()


@pytest.mark.asyncio
async def test_launch_login_context_uses_persistent_context_when_enabled(monkeypatch, tmp_path):
	calls = {}
	context = SimpleNamespace()

	async def fake_launch_persistent_context_async(profile_dir, **kwargs):
		calls['profile_dir'] = profile_dir
		calls['kwargs'] = kwargs
		return context

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_persistent_context_async=fake_launch_persistent_context_async),
	)

	settings = load_browser_login_settings('Account 1', 'anyrouter', persist_profile=True)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'anyrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
	)

	result = await launch_login_context(settings)

	assert result is cast(Any, context)
	assert calls['profile_dir'] == str(settings.profile_dir)
	assert 'args' not in calls['kwargs']


@pytest.mark.asyncio
async def test_launch_login_context_adds_host_resolver_rules(monkeypatch, tmp_path):
	calls = {}
	context = SimpleNamespace()

	async def fake_launch_persistent_context_async(profile_dir, **kwargs):
		calls['profile_dir'] = profile_dir
		calls['kwargs'] = kwargs
		return context

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_persistent_context_async=fake_launch_persistent_context_async),
	)

	settings = load_browser_login_settings('Account 1', 'anyrouter', persist_profile=True)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'anyrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
	)

	result = await launch_login_context(settings, host_fallbacks={'anyrouter.top': '47.246.23.192'})

	assert result is cast(Any, context)
	assert calls['profile_dir'] == str(settings.profile_dir)
	assert calls['kwargs']['args'] == ['--host-resolver-rules=MAP anyrouter.top 47.246.23.192']


@pytest.mark.asyncio
async def test_launch_login_context_closes_browser_for_ephemeral_context(monkeypatch, tmp_path):
	class FakeContext:
		def __init__(self):
			self.closed = False

		async def close(self):
			self.closed = True

	class FakeBrowser:
		def __init__(self):
			self.context = FakeContext()
			self.closed = False
			self.context_kwargs = {}
			self.launch_kwargs = {}

		async def new_context(self, **kwargs):
			self.context_kwargs = kwargs
			return self.context

		async def close(self):
			self.closed = True

	browser = FakeBrowser()

	async def fake_launch_async(**kwargs):
		browser.launch_kwargs = kwargs
		return browser

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_async=fake_launch_async),
	)

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'agentrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
	)

	context = await launch_login_context(settings)
	await context.close()

	assert cast(Any, context).closed is True
	assert browser.closed is True
	assert not settings.profile_dir.exists()
