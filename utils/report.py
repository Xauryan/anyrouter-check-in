"""签到结果持久化与 GitHub Actions 摘要渲染。"""

import html
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

RESULTS_FILE_ENV = 'CHECKIN_RESULTS_FILE'
REPORT_SCHEMA_VERSION = 2

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


@dataclass
class CheckInRunReport:
	"""一次签到运行的公开结果。"""

	started_at: str
	finished_at: str | None = None
	accounts: list[AccountCheckInResult] = field(default_factory=list)
	error: str | None = None

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
