#!/usr/bin/env python3
"""将签到结果文件发布为 GitHub Actions Job Summary。"""

import argparse
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from utils.report import load_check_in_report, render_github_summary


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='生成 AnyRouter GitHub Actions 签到摘要')
	parser.add_argument('--input', required=True, help='checkin.py 生成的 JSON 结果文件')
	parser.add_argument('--output', help='Markdown 输出文件；省略时写入标准输出')
	parser.add_argument('--step-outcome', help='“执行签到”步骤的 GitHub Actions outcome')
	return parser.parse_args()


def main() -> int:
	args = parse_args()
	report_path = Path(args.input)
	report = None
	if report_path.is_file():
		try:
			report = load_check_in_report(report_path)
		except (OSError, ValueError):
			report = None

	summary = render_github_summary(report, step_outcome=args.step_outcome)
	output_path = args.output or os.getenv('GITHUB_STEP_SUMMARY', '').strip()
	if output_path:
		with Path(output_path).open('a', encoding='utf-8') as output:
			output.write(summary)
	else:
		print(summary, end='')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
