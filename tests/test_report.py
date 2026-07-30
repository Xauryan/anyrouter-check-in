import json
import subprocess
import sys
from pathlib import Path

import pytest

import checkin
from utils.config import AccountConfig, AppConfig
from utils.report import (
	AccountCheckInResult,
	BatchRunInfo,
	CheckInRunReport,
	CheckInStatus,
	load_check_in_report,
	merge_check_in_reports,
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
			AccountCheckInResult(
				name='备用账号',
				provider='agentrouter',
				status='failure',
				reason='登录或用户信息获取失败（已重试）',
			),
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
		'reason': None,
	}
	assert set(payload['accounts'][0]) == {'name', 'provider', 'status', 'balance', 'used', 'reason'}


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
			AccountCheckInResult(
				name='backup',
				provider='agentrouter',
				status='failure',
				reason='登录失败 | <重试>',
			),
			AccountCheckInResult(name='waiting', provider='custom', status='pending'),
		],
	)

	summary = render_github_summary(report.to_dict(), step_outcome='success')

	assert '# AnyRouter 签到结果' in summary
	assert '✅ **1** 成功 / ❌ **1** 失败 / ⚪ **1** 未完成' in summary
	assert 'main&#124;&lt;admin&gt;' in summary
	assert '| backup | agentrouter | ❌ 失败 | 未获取 | 未获取 | 登录失败 &#124; &lt;重试&gt; |' in summary
	assert '| waiting | custom | ⚪ 未完成 | 未获取 | 未获取 | — |' in summary
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
	assert '| 账号 1 | anyrouter | ✅ 成功 | $25.00 | $5.00 | — |' in summary
	assert '- 签到步骤：✅ 成功' in summary


def _batch_report(
	*,
	index: int,
	account_start: int,
	names: list[str],
	fingerprint: str,
	status: CheckInStatus = 'success',
) -> CheckInRunReport:
	return CheckInRunReport(
		started_at=f'2026-07-30T08:0{index}:00+00:00',
		finished_at=f'2026-07-30T08:1{index}:00+00:00',
		accounts=[
			AccountCheckInResult(
				name=name,
				provider='anyrouter',
				status=status,
				balance=100.0 if status == 'success' else None,
				used=20.0 if status == 'success' else None,
				reason=None if status == 'success' else '登录验证失败（已重试）',
			)
			for name in names
		],
		batch=BatchRunInfo(
			index=index,
			count=3,
			total_accounts=5,
			account_start=account_start,
			account_end=account_start + len(names) - 1,
			account_count=len(names),
			egress_fingerprint=fingerprint,
		),
	)


def test_merge_check_in_reports_preserves_order_and_runner_fingerprints():
	reports = [
		_batch_report(index=2, account_start=5, names=['账号 5'], fingerprint='cccccccccccc').to_dict(),
		_batch_report(
			index=0,
			account_start=1,
			names=['账号 1', '账号 2'],
			fingerprint='aaaaaaaaaaaa',
		).to_dict(),
		_batch_report(
			index=1,
			account_start=3,
			names=['账号 3', '账号 4'],
			fingerprint='bbbbbbbbbbbb',
			status='failure',
		).to_dict(),
	]

	merged = merge_check_in_reports(reports, expected_batches=3)
	payload = merged.to_dict()
	summary = render_github_summary(payload, step_outcome='success')

	assert [account['name'] for account in payload['accounts']] == [
		'账号 1',
		'账号 2',
		'账号 3',
		'账号 4',
		'账号 5',
	]
	assert payload['success_count'] == 3
	assert payload['failure_count'] == 2
	assert payload['pending_count'] == 0
	assert payload['error'] is None
	assert [batch['index'] for batch in payload['batches']] == [0, 1, 2]
	assert '| 1 | 1–2 | 2 | aaaaaaaaaaaa |' in summary
	assert '| 2 | 3–4 | 2 | bbbbbbbbbbbb |' in summary
	assert '| 3 | 5–5 | 1 | cccccccccccc |' in summary
	assert '各批次出口 IP 指纹不同' in summary


def test_merge_check_in_reports_marks_missing_batch_incomplete():
	reports = [
		_batch_report(index=0, account_start=1, names=['账号 1', '账号 2'], fingerprint='same').to_dict(),
		_batch_report(index=2, account_start=5, names=['账号 5'], fingerprint='same').to_dict(),
	]

	merged = merge_check_in_reports(reports, expected_batches=3)

	assert merged.finished_at is None
	assert merged.error is not None
	assert '缺少批次：2' in merged.error
	assert '合并账号数量不符：期望 5，实际 3' in merged.error


def test_merge_script_writes_combined_report_and_github_outputs(tmp_path):
	input_dir = tmp_path / 'downloaded'
	for batch in [
		_batch_report(index=0, account_start=1, names=['账号 1', '账号 2'], fingerprint='aaaaaaaaaaaa'),
		_batch_report(index=1, account_start=3, names=['账号 3', '账号 4'], fingerprint='bbbbbbbbbbbb'),
		_batch_report(index=2, account_start=5, names=['账号 5'], fingerprint='cccccccccccc'),
	]:
		assert batch.batch is not None
		batch_dir = input_dir / f'batch-{batch.batch.index}'
		save_check_in_report(batch, str(batch_dir / 'checkin_results.json'))

	merged_path = tmp_path / 'merged.json'
	github_output = tmp_path / 'github-output.txt'
	project_root = Path(__file__).parent.parent
	subprocess.run(
		[
			sys.executable,
			str(project_root / 'scripts' / 'merge_checkin_reports.py'),
			'--input-dir',
			str(input_dir),
			'--output',
			str(merged_path),
			'--expected-batches',
			'3',
			'--github-output',
			str(github_output),
		],
		check=True,
		cwd=project_root,
	)

	payload = load_check_in_report(merged_path)
	assert payload['success_count'] == 5
	assert github_output.read_text(encoding='utf-8').splitlines() == [
		'success_count=5',
		'complete=true',
		'step_outcome=success',
	]


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
	monkeypatch.delenv('CHECKIN_BATCH_INDEX', raising=False)
	monkeypatch.delenv('CHECKIN_BATCH_COUNT', raising=False)
	monkeypatch.delenv('CHECKIN_EGRESS_FINGERPRINT', raising=False)
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
	assert payload['batch'] == {
		'index': 0,
		'count': 1,
		'total_accounts': 2,
		'account_start': 1,
		'account_end': 2,
		'account_count': 2,
		'egress_fingerprint': None,
	}
	assert payload['accounts'] == [
		{
			'name': '成功账号',
			'provider': 'anyrouter',
			'status': 'success',
			'balance': 125.0,
			'used': 20.0,
			'reason': None,
		},
		{
			'name': '失败账号',
			'provider': 'agentrouter',
			'status': 'failure',
			'balance': None,
			'used': None,
			'reason': '登录验证失败（已重试）',
		},
	]

	serialized_report = report_path.read_text(encoding='utf-8')
	assert 'secret@example.com' not in serialized_report
	assert 'super-secret-password' not in serialized_report
	assert 'secret-session-cookie' not in serialized_report
	assert 'secret-api-user' not in serialized_report
