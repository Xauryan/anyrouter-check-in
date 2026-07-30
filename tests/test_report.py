import json
import subprocess
import sys
from pathlib import Path

import pytest

import checkin
from utils.config import AccountConfig, AppConfig
from utils.report import (
	AccountCheckInResult,
	CheckInRunReport,
	load_check_in_report,
	render_github_summary,
	save_check_in_report,
)


def test_report_contains_public_results_and_statistics_only(tmp_path):
	report_path = tmp_path / 'checkin-results.json'
	report = CheckInRunReport(
		started_at='2026-07-30T16:00:00+08:00',
		finished_at='2026-07-30T16:05:00+08:00',
		accounts=[
			AccountCheckInResult(
				name='主账号',
				provider='anyrouter',
				status='success',
				balance=125.5,
				used=20.25,
			),
			AccountCheckInResult(name='备用账号', provider='agentrouter', status='failure'),
		],
	)

	save_check_in_report(report, str(report_path))
	payload = load_check_in_report(report_path)

	assert payload['success_count'] == 1
	assert payload['failure_count'] == 1
	assert payload['pending_count'] == 0
	assert payload['accounts'][0] == {
		'name': '主账号',
		'provider': 'anyrouter',
		'status': 'success',
		'balance': 125.5,
		'used': 20.25,
	}
	assert set(payload['accounts'][0]) == {'name', 'provider', 'status', 'balance', 'used'}


def test_render_github_summary_shows_every_account_and_escapes_names():
	report = CheckInRunReport(
		started_at='2026-07-30T08:00:00+00:00',
		finished_at='2026-07-30T08:05:00+00:00',
		accounts=[
			AccountCheckInResult(
				name='main|<admin>',
				provider='anyrouter',
				status='success',
				balance=2120.934,
				used=1104.066,
			),
			AccountCheckInResult(name='backup', provider='agentrouter', status='failure'),
			AccountCheckInResult(name='waiting', provider='custom', status='pending'),
		],
	)

	summary = render_github_summary(report.to_dict(), step_outcome='success')

	assert '# AnyRouter 签到结果' in summary
	assert '✅ **1** 成功 / ❌ **1** 失败 / ⚪ **1** 未完成' in summary
	assert 'main&#124;&lt;admin&gt;' in summary
	assert '| backup | agentrouter | ❌ 失败 | 未获取 | 未获取 |' in summary
	assert '| waiting | custom | ⚪ 未完成 | 未获取 | 未获取 |' in summary
	assert '$2,120.93' in summary
	assert '$1,104.07' in summary
	assert '- 签到步骤：✅ 成功' in summary
	assert '<admin>' not in summary


def test_render_github_summary_handles_missing_result_file():
	summary = render_github_summary(None, step_outcome='failure')

	assert '签到脚本未生成结果文件' in summary
	assert '- 签到步骤：❌ 失败' in summary


def test_render_script_appends_summary_file(tmp_path):
	report_path = tmp_path / 'checkin-results.json'
	summary_path = tmp_path / 'step-summary.md'
	report = CheckInRunReport(
		started_at='2026-07-30T08:00:00+00:00',
		finished_at='2026-07-30T08:05:00+00:00',
		accounts=[
			AccountCheckInResult(
				name='账号 1',
				provider='anyrouter',
				status='success',
				balance=25.0,
				used=5.0,
			)
		],
	)
	save_check_in_report(report, str(report_path))
	project_root = Path(__file__).parent.parent

	subprocess.run(
		[
			sys.executable,
			str(project_root / 'scripts' / 'render_checkin_summary.py'),
			'--input',
			str(report_path),
			'--output',
			str(summary_path),
			'--step-outcome',
			'success',
		],
		check=True,
		cwd=project_root,
	)

	summary = summary_path.read_text(encoding='utf-8')
	assert '| 账号 1 | anyrouter | ✅ 成功 | $25.00 | $5.00 |' in summary
	assert '- 签到步骤：✅ 成功' in summary


@pytest.mark.asyncio
async def test_main_persists_each_account_result_without_credentials(monkeypatch, tmp_path):
	report_path = tmp_path / 'checkin-results.json'
	accounts = [
		AccountConfig(
			cookies=None,
			provider='anyrouter',
			name='成功账号',
			email='secret@example.com',
			password='super-secret-password',
		),
		AccountConfig(
			cookies={'session': 'secret-session-cookie'},
			api_user='secret-api-user',
			provider='agentrouter',
			name='失败账号',
		),
	]
	account_outcomes = [
		(
			True,
			{'success': True, 'quota': 100.0, 'used_quota': 20.0},
			{'success': True, 'quota': 125.0, 'used_quota': 20.0},
		),
		(False, None, None),
	]

	async def fake_check_in_account(account, account_index, app_config):
		return account_outcomes[account_index]

	app_config = AppConfig(providers={})
	monkeypatch.setenv('CHECKIN_RESULTS_FILE', str(report_path))
	monkeypatch.delenv('DEBUG_MODE', raising=False)
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', classmethod(lambda cls: app_config))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', lambda balance_hash: None)
	monkeypatch.setattr(checkin.notify, 'push_message', lambda *args, **kwargs: None)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	payload = json.loads(report_path.read_text(encoding='utf-8'))
	assert payload['finished_at']
	assert payload['success_count'] == 1
	assert payload['failure_count'] == 1
	assert payload['accounts'] == [
		{
			'name': '成功账号',
			'provider': 'anyrouter',
			'status': 'success',
			'balance': 125.0,
			'used': 20.0,
		},
		{
			'name': '失败账号',
			'provider': 'agentrouter',
			'status': 'failure',
			'balance': None,
			'used': None,
		},
	]

	serialized_report = report_path.read_text(encoding='utf-8')
	assert 'secret@example.com' not in serialized_report
	assert 'super-secret-password' not in serialized_report
	assert 'secret-session-cookie' not in serialized_report
	assert 'secret-api-user' not in serialized_report
