"""签到结果持久化与 GitHub Actions 摘要渲染。"""

import html
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

RESULTS_FILE_ENV = 'CHECKIN_RESULTS_FILE'
REPORT_SCHEMA_VERSION = 3

CheckInStatus = Literal['pending', 'success', 'failure']


@dataclass
class AccountCheckInResult:
	"""单个账号可公开展示的签到结果。

	这里只保存显示名称、服务商和余额等结果字段，禁止加入邮箱、密码、
	Cookie、API User 等认证信息。
	"""

	name: str
	provider: str
	status: CheckInStatus = 'pending'
	balance: float | None = None
	used: float | None = None
	reason: str | None = None


@dataclass(frozen=True)
class BatchRunInfo:
	"""可公开展示的批次与 Runner 网络诊断信息。"""

	index: int
	count: int
	total_accounts: int
	account_start: int
	account_end: int
	account_count: int
	egress_fingerprint: str | None = None


@dataclass
class CheckInRunReport:
	"""一次签到运行的公开结果。"""

	started_at: str
	finished_at: str | None = None
	accounts: list[AccountCheckInResult] = field(default_factory=list)
	error: str | None = None
	batch: BatchRunInfo | None = None
	batches: list[BatchRunInfo] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		"""转换成带版本号和统计信息的 JSON 对象。"""
		success_count = sum(account.status == 'success' for account in self.accounts)
		failure_count = sum(account.status == 'failure' for account in self.accounts)
		pending_count = sum(account.status == 'pending' for account in self.accounts)

		return {
			'schema_version': REPORT_SCHEMA_VERSION,
			'started_at': self.started_at,
			'finished_at': self.finished_at,
			'success_count': success_count,
			'failure_count': failure_count,
			'pending_count': pending_count,
			'accounts': [asdict(account) for account in self.accounts],
			'error': self.error,
			'batch': asdict(self.batch) if self.batch else None,
			'batches': [asdict(batch) for batch in self.batches],
		}


def save_check_in_report(report: CheckInRunReport, path: str | None = None) -> None:
	"""原子写入签到结果；未配置结果路径时静默跳过。"""
	report_path = (path if path is not None else os.getenv(RESULTS_FILE_ENV, '')).strip()
	if not report_path:
		return

	target = Path(report_path)
	target.parent.mkdir(parents=True, exist_ok=True)
	temporary = target.with_name(f'.{target.name}.tmp')
	temporary.write_text(
		json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + '\n',
		encoding='utf-8',
	)
	temporary.replace(target)


def load_check_in_report(path: str | Path) -> dict[str, Any]:
	"""读取并做最低限度校验，避免无效结果生成误导性摘要。"""
	payload = json.loads(Path(path).read_text(encoding='utf-8'))
	if not isinstance(payload, dict):
		raise ValueError('签到结果必须是 JSON 对象')
	if payload.get('schema_version') != REPORT_SCHEMA_VERSION:
		raise ValueError('不支持的签到结果版本')
	if not isinstance(payload.get('accounts'), list):
		raise ValueError('签到结果缺少账号列表')
	return payload


def _strict_int(value: object) -> int | None:
	if isinstance(value, bool) or not isinstance(value, int):
		return None
	return value


def _public_money(value: object) -> float | None:
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return None
	if not math.isfinite(value):
		return None
	return float(value)


def _public_account(payload: object) -> AccountCheckInResult | None:
	"""只从批次 artifact 中提取允许公开聚合的账号字段。"""
	if not isinstance(payload, dict):
		return None

	raw_status = payload.get('status')
	status = cast('CheckInStatus', raw_status) if raw_status in {'pending', 'success', 'failure'} else 'pending'
	name = payload.get('name')
	provider = payload.get('provider')
	reason = payload.get('reason')
	return AccountCheckInResult(
		name=name if isinstance(name, str) and name else '未命名账号',
		provider=provider if isinstance(provider, str) and provider else '未知',
		status=status,
		balance=_public_money(payload.get('balance')),
		used=_public_money(payload.get('used')),
		reason=reason if isinstance(reason, str) and reason else None,
	)


def _batch_info_from_payload(payload: object, expected_batches: int) -> BatchRunInfo | None:
	if not isinstance(payload, dict):
		return None

	index = _strict_int(payload.get('index'))
	count = _strict_int(payload.get('count'))
	total_accounts = _strict_int(payload.get('total_accounts'))
	account_start = _strict_int(payload.get('account_start'))
	account_end = _strict_int(payload.get('account_end'))
	account_count = _strict_int(payload.get('account_count'))
	if (
		index is None
		or count != expected_batches
		or total_accounts is None
		or account_start is None
		or account_end is None
		or account_count is None
		or index < 0
		or index >= expected_batches
		or total_accounts < 0
		or account_start < 0
		or account_end < 0
		or account_count < 0
	):
		return None

	fingerprint = payload.get('egress_fingerprint')
	if not isinstance(fingerprint, str) or not fingerprint.strip():
		fingerprint = None
	else:
		fingerprint = fingerprint.strip()[:64]

	return BatchRunInfo(
		index=index,
		count=count,
		total_accounts=total_accounts,
		account_start=account_start,
		account_end=account_end,
		account_count=account_count,
		egress_fingerprint=fingerprint,
	)


def merge_check_in_reports(
	reports: list[dict[str, Any]],
	*,
	expected_batches: int,
) -> CheckInRunReport:
	"""合并 matrix 批次结果，并显式标记缺失、重复或不完整的批次。"""
	if expected_batches < 1:
		raise ValueError('expected_batches 必须大于等于 1')

	reports_by_batch: dict[int, tuple[dict[str, Any], BatchRunInfo]] = {}
	errors: list[str] = []
	for report in reports:
		batch = _batch_info_from_payload(report.get('batch'), expected_batches)
		if batch is None:
			errors.append('发现缺少有效批次元数据的结果')
			continue
		if batch.index in reports_by_batch:
			errors.append(f'批次 {batch.index + 1} 结果重复')
			continue
		reports_by_batch[batch.index] = (report, batch)

	missing_batches = [index + 1 for index in range(expected_batches) if index not in reports_by_batch]
	if missing_batches:
		errors.append(f'缺少批次：{", ".join(map(str, missing_batches))}')

	accounts: list[AccountCheckInResult] = []
	batches: list[BatchRunInfo] = []
	started_at_values: list[str] = []
	finished_at_values: list[str] = []
	total_account_values: set[int] = set()

	for index in sorted(reports_by_batch):
		report, batch = reports_by_batch[index]
		batches.append(batch)
		total_account_values.add(batch.total_accounts)

		started_at = report.get('started_at')
		if isinstance(started_at, str) and started_at:
			started_at_values.append(started_at)
		finished_at = report.get('finished_at')
		if isinstance(finished_at, str) and finished_at:
			finished_at_values.append(finished_at)
		else:
			errors.append(f'批次 {index + 1} 未正常完成')

		raw_accounts = report.get('accounts')
		if not isinstance(raw_accounts, list):
			errors.append(f'批次 {index + 1} 缺少账号列表')
			continue

		public_accounts = [account for item in raw_accounts if (account := _public_account(item)) is not None]
		if len(public_accounts) != len(raw_accounts):
			errors.append(f'批次 {index + 1} 包含无效账号结果')
		if len(public_accounts) != batch.account_count:
			errors.append(f'批次 {index + 1} 账号数量不符：期望 {batch.account_count}，实际 {len(public_accounts)}')
		accounts.extend(public_accounts)

		report_error = report.get('error')
		if isinstance(report_error, str) and report_error:
			errors.append(f'批次 {index + 1}：{report_error}')

	if len(total_account_values) > 1:
		errors.append('各批次记录的总账号数不一致')
	expected_total = next(iter(total_account_values), 0)
	if len(accounts) != expected_total:
		errors.append(f'合并账号数量不符：期望 {expected_total}，实际 {len(accounts)}')

	unique_errors = list(dict.fromkeys(errors))
	return CheckInRunReport(
		started_at=min(started_at_values) if started_at_values else '',
		finished_at=max(finished_at_values) if len(finished_at_values) == expected_batches else None,
		accounts=accounts,
		error='；'.join(unique_errors) if unique_errors else None,
		batches=batches,
	)


def _escape_table_cell(value: object) -> str:
	"""转义账号自定义名称，防止破坏 Markdown 表格或注入 HTML。"""
	return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\r', ' ').replace('\n', ' ')


def _format_money(value: object) -> str:
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return '未获取'
	if not math.isfinite(value):
		return '未获取'
	return f'${value:,.2f}'


def _format_status(status: object) -> str:
	status_key = status if isinstance(status, str) else ''
	status_labels: dict[str, str] = {
		'success': '✅ 成功',
		'failure': '❌ 失败',
		'pending': '⚪ 未完成',
	}
	return status_labels.get(status_key, '⚪ 未知')


def _format_reason(reason: object) -> str:
	if not isinstance(reason, str) or not reason.strip():
		return '—'
	return _escape_table_cell(reason)


def _format_step_outcome(step_outcome: str | None) -> str:
	return {
		'success': '✅ 成功',
		'failure': '❌ 失败',
		'cancelled': '⏹️ 已取消',
		'skipped': '⏭️ 已跳过',
	}.get(step_outcome or '', '⚪ 未知')


def render_github_summary(report: dict[str, Any] | None, *, step_outcome: str | None = None) -> str:
	"""把签到结果渲染成 GitHub Flavored Markdown。"""
	lines = ['# AnyRouter 签到结果', '']

	if report is None:
		lines.extend(
			[
				'> ⚠️ 签到脚本未生成结果文件，无法展示账号明细。请查看“执行签到”步骤日志。',
				'',
				f'- 签到步骤：{_format_step_outcome(step_outcome)}',
				'',
			]
		)
		return '\n'.join(lines)

	accounts = report.get('accounts', [])
	if not isinstance(accounts, list):
		accounts = []

	success_count = sum(isinstance(account, dict) and account.get('status') == 'success' for account in accounts)
	failure_count = sum(isinstance(account, dict) and account.get('status') == 'failure' for account in accounts)
	pending_count = len(accounts) - success_count - failure_count

	lines.extend(
		[
			f'> 共 **{len(accounts)}** 个账号：✅ **{success_count}** 成功 / '
			f'❌ **{failure_count}** 失败 / ⚪ **{pending_count}** 未完成',
			'',
		]
	)

	if accounts:
		lines.extend(
			[
				'| 账号 | 服务商 | 签到状态 | 当前余额 | 累计消耗 | 说明 |',
				'|:--|:--|:--:|--:|--:|:--|',
			]
		)
		for account in accounts:
			if not isinstance(account, dict):
				continue
			lines.append(
				'| '
				f'{_escape_table_cell(account.get("name", "未命名账号"))} | '
				f'{_escape_table_cell(account.get("provider", "未知"))} | '
				f'{_format_status(account.get("status"))} | '
				f'{_format_money(account.get("balance"))} | '
				f'{_format_money(account.get("used"))} | '
				f'{_format_reason(account.get("reason"))} |'
			)
	else:
		lines.extend(['> 没有可展示的账号结果。', ''])

	batches = report.get('batches')
	if isinstance(batches, list) and batches:
		lines.extend(
			[
				'',
				'## Runner 出口诊断',
				'',
				'| 批次 | 账号范围 | 账号数 | 出口 IP 指纹 |',
				'|:--:|:--:|--:|:--|',
			]
		)
		fingerprints: list[str] = []
		for batch in batches:
			if not isinstance(batch, dict):
				continue
			index = batch.get('index')
			batch_number = index + 1 if isinstance(index, int) and not isinstance(index, bool) else '未知'
			account_start = batch.get('account_start')
			account_end = batch.get('account_end')
			account_range = (
				f'{account_start}–{account_end}'
				if isinstance(account_start, int)
				and not isinstance(account_start, bool)
				and isinstance(account_end, int)
				and not isinstance(account_end, bool)
				else '未知'
			)
			account_count = batch.get('account_count')
			account_count_label = (
				str(account_count) if isinstance(account_count, int) and not isinstance(account_count, bool) else '未知'
			)
			fingerprint = batch.get('egress_fingerprint')
			fingerprint_label = fingerprint if isinstance(fingerprint, str) and fingerprint else '未获取'
			if fingerprint_label != '未获取':
				fingerprints.append(fingerprint_label)
			lines.append(
				'| '
				f'{_escape_table_cell(batch_number)} | '
				f'{_escape_table_cell(account_range)} | '
				f'{_escape_table_cell(account_count_label)} | '
				f'{_escape_table_cell(fingerprint_label)} |'
			)

		lines.append('')
		if len(fingerprints) == len(batches) and len(set(fingerprints)) == len(fingerprints):
			lines.append('> ✅ 各批次出口 IP 指纹不同，说明本次 matrix jobs 实际使用了不同公网出口。')
		elif len(fingerprints) == len(batches):
			lines.append('> ⚠️ 至少两个批次的出口 IP 指纹相同，GitHub 未为所有 jobs 提供不同公网出口。')
		else:
			lines.append('> ⚪ 部分批次未获取出口 IP 指纹，无法完整比较公网出口。')
		lines.append('')
		lines.append('> 指纹仅用于同一次运行内比较，不包含原始公网 IP。')

	error = report.get('error')
	if isinstance(error, str) and error:
		lines.extend(['', f'> ❌ {_escape_table_cell(error)}'])

	lines.extend(
		[
			'',
			'<details>',
			'<summary>运行信息</summary>',
			'',
			f'- 签到步骤：{_format_step_outcome(step_outcome)}',
			f'- 开始时间：{_escape_table_cell(report.get("started_at") or "未知")}',
			f'- 完成时间：{_escape_table_cell(report.get("finished_at") or "未正常完成")}',
			'',
			'</details>',
			'',
			'> 余额来自签到完成后的用户信息查询；“未获取”表示认证或余额查询未成功。失败说明为脱敏后的分类信息。',
			'',
		]
	)
	return '\n'.join(lines)
