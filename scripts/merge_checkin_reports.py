#!/usr/bin/env python3
"""合并 GitHub Actions matrix 批次生成的公开签到结果。"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from utils.report import load_check_in_report, merge_check_in_reports, save_check_in_report


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='合并 AnyRouter 分批签到结果')
	parser.add_argument('--input-dir', required=True, help='download-artifact 下载目录')
	parser.add_argument('--output', required=True, help='合并后的 JSON 结果文件')
	parser.add_argument('--expected-batches', required=True, type=int, help='预期批次数')
	parser.add_argument('--github-output', help='可选的 GitHub Actions GITHUB_OUTPUT 文件')
	return parser.parse_args()


def _append_github_outputs(path: str | None, *, success_count: int, complete: bool) -> None:
	if not path:
		return
	with Path(path).open('a', encoding='utf-8') as output:
		output.write(f'success_count={success_count}\n')
		output.write(f'complete={str(complete).lower()}\n')
		output.write(f'step_outcome={"success" if success_count > 0 and complete else "failure"}\n')


def main() -> int:
	args = parse_args()
	input_dir = Path(args.input_dir)
	report_paths = sorted(input_dir.rglob('checkin_results.json')) if input_dir.is_dir() else []
	reports = []
	for report_path in report_paths:
		try:
			reports.append(load_check_in_report(report_path))
		except (OSError, ValueError) as exc:
			print(f'[WARN] 忽略无效批次结果 {report_path}: {exc}')

	merged = merge_check_in_reports(reports, expected_batches=args.expected_batches)
	save_check_in_report(merged, args.output)
	payload = merged.to_dict()
	success_count = payload['success_count']
	complete = merged.error is None and payload['pending_count'] == 0
	_append_github_outputs(
		args.github_output,
		success_count=success_count,
		complete=complete,
	)
	print(
		f'[INFO] 合并 {len(reports)}/{args.expected_batches} 个批次：'
		f'{success_count} 成功 / {payload["failure_count"]} 失败 / {payload["pending_count"]} 未完成'
	)
	if merged.error:
		print(f'[WARN] 合并结果不完整：{merged.error}')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
