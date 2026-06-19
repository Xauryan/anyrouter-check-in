import json

from utils.config import AppConfig, ProviderConfig


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
