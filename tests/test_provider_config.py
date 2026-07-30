import json

import pytest

from utils.config import (
	AccountConfig,
	AppConfig,
	ProviderConfig,
	select_account_batch,
	select_account_batch_from_env,
)


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is False
	assert config.providers['anyrouter'].host_fallbacks == {'anyrouter.top': '47.246.23.192'}
	assert config.providers['agentrouter'].host_fallbacks == {}


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True
	assert config.providers['anyrouter'].host_fallbacks == {'anyrouter.top': '47.246.23.192'}


def test_custom_provider_profile_persistence_defaults_to_false(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert config.providers['custom'].persist_profile is False


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(
		name='custom',
		domain='https://old.example.com',
		persist_profile=True,
		host_fallbacks={'old.example.com': '192.0.2.10'},
	)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True
	assert provider.host_fallbacks == {'old.example.com': '192.0.2.10'}


def test_provider_host_fallbacks_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{'anyrouter': {'domain': 'https://anyrouter.top', 'host_fallbacks': {'anyrouter.top': '192.0.2.1'}}}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].host_fallbacks == {'anyrouter.top': '192.0.2.1'}


def test_provider_host_fallbacks_can_be_disabled(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps({'anyrouter': {'domain': 'https://anyrouter.top', 'host_fallbacks': {}}}),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].host_fallbacks == {}


def _accounts(count: int) -> list[AccountConfig]:
	return [
		AccountConfig(
			cookies={'session': f'session-{index}'},
			api_user=str(index),
			name=f'账号 {index + 1}',
		)
		for index in range(count)
	]


def test_select_account_batch_balances_eleven_accounts_into_four_four_three():
	accounts = _accounts(11)

	selections = [select_account_batch(accounts, batch_index, 3) for batch_index in range(3)]

	assert [len(selection.accounts) for selection in selections] == [4, 4, 3]
	assert [(selection.start_index, selection.end_index) for selection in selections] == [(0, 4), (4, 8), (8, 11)]
	assert [[account.name for account in selection.accounts] for selection in selections] == [
		['账号 1', '账号 2', '账号 3', '账号 4'],
		['账号 5', '账号 6', '账号 7', '账号 8'],
		['账号 9', '账号 10', '账号 11'],
	]
	assert all(selection.total_accounts == 11 for selection in selections)


def test_select_account_batch_from_env_defaults_to_all_accounts(monkeypatch):
	accounts = _accounts(2)
	monkeypatch.delenv('CHECKIN_BATCH_INDEX', raising=False)
	monkeypatch.delenv('CHECKIN_BATCH_COUNT', raising=False)

	selection = select_account_batch_from_env(accounts)

	assert selection.accounts == accounts
	assert selection.batch_index == 0
	assert selection.batch_count == 1


@pytest.mark.parametrize(
	('batch_index', 'batch_count'),
	[
		(0, 0),
		(-1, 3),
		(3, 3),
	],
)
def test_select_account_batch_rejects_invalid_coordinates(batch_index, batch_count):
	with pytest.raises(ValueError):
		select_account_batch(_accounts(3), batch_index, batch_count)


def test_select_account_batch_from_env_rejects_non_integer_values(monkeypatch):
	monkeypatch.setenv('CHECKIN_BATCH_INDEX', 'first')
	monkeypatch.setenv('CHECKIN_BATCH_COUNT', 'three')

	with pytest.raises(ValueError, match='必须是整数'):
		select_account_batch_from_env(_accounts(3))
