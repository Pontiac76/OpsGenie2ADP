#!/usr/bin/env python3

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set
from datetime import time
from collections import defaultdict
import os
import io
import tempfile
import zipfile
from colorama import init
init()

SUBMIT_COLOR = "\033[97m"
OVERLAP_COLOR = "\033[90m"
RESET_COLOR = "\033[0m"
DT_FORMAT = "%Y/%m/%d %H:%M:%S.%f%z"


@dataclass
class Alert:
	tiny_id: int
	created_at_ms: int
	created_at: datetime
	message: str
	owner: str
	teams: str
	raw: dict
	absorbed: bool = False


@dataclass
class PayBlock:
	anchor: Alert
	duration_minutes: int
	end_time: datetime
	absorbed_ids: List[int] = field(default_factory=list)

def open_opsgenie_csv_stream(csv_path: Optional[Path], zip_path: Optional[Path]):
	if csv_path and zip_path:
		raise ValueError("Use either --csv or --zip, not both.")

	if not csv_path and not zip_path:
		raise ValueError("You must provide either --csv or --zip.")

	if csv_path:
		return csv_path.open("r", newline="", encoding="utf-8-sig")

	with zipfile.ZipFile(zip_path, "r") as zf:
		csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]

		if not csv_names:
			raise ValueError(f"No CSV file found inside zip: {zip_path}")

		if len(csv_names) > 1:
			raise ValueError(
				f"Multiple CSV files found inside zip: {', '.join(csv_names)}. "
				"Keep one CSV per zip for now."
			)

		with zf.open(csv_names[0], "r") as raw_file:
			text_stream = io.TextIOWrapper(raw_file, encoding="utf-8-sig", newline="")
			data = text_stream.read()

	return io.StringIO(data)


def build_default_pay_blocks(
	alerts: List[Alert],
	forced_ids: Set[int],
	time_overrides: dict[int, int],
	default_minutes: int = 60,
) -> List[PayBlock]:
	blocks: List[PayBlock] = []

	for alert in alerts:

		if alert.absorbed and alert.tiny_id not in forced_ids:
			continue

		duration_minutes = time_overrides.get(alert.tiny_id, default_minutes)
		end_time = alert.created_at + timedelta(minutes=duration_minutes)

		block = PayBlock(
			anchor=alert,
			duration_minutes=duration_minutes,
			end_time=end_time,
		)

		for later in alerts:
			if later.created_at <= alert.created_at:
				continue

			if later.created_at < end_time:
				if later.tiny_id not in forced_ids:
					later.absorbed = True
					block.absorbed_ids.append(later.tiny_id)

		blocks.append(block)

	return blocks



def render_full_report(alerts: List[Alert], blocks: List[PayBlock], forced_ids: Set[int]) -> str:
	from collections import defaultdict

	submit_ids = {block.anchor.tiny_id for block in blocks}
	grouped = defaultdict(list)

	for alert in alerts:
		grouped[alert.created_at.date()].append(alert)

	lines = []

	for day_date in sorted(grouped.keys()):
		day_alerts = grouped[day_date]

		lines.append(
			f"{day_alerts[0].created_at.strftime('%A')} - {format_long_date(day_alerts[0].created_at)}"
		)

		for alert in day_alerts:

			if alert.tiny_id in forced_ids:
				color = SUBMIT_COLOR
				status = "FORCED "
			elif alert.tiny_id in submit_ids:
				color = SUBMIT_COLOR
				status = "SUBMIT "
			else:
				color = OVERLAP_COLOR
				status = "OVERLAP"

			lines.append(
				f"{color}{status} | {alert.tiny_id} - {format_time(alert.created_at)}: {alert.message}{RESET_COLOR}"
			)

		lines.append("")

	return "\n".join(lines).rstrip() + "\n"

def format_long_date(dt: datetime) -> str:
	return dt.strftime("%d %B %Y")

def get_oncall_window(reference_dt: datetime) -> tuple[str, str]:
	days_back = (reference_dt.weekday() - 4) % 7
	start_date = (reference_dt - timedelta(days=days_back)).date()

	if reference_dt.weekday() == 4 and reference_dt.time() < time(17, 0):
		start_date = start_date - timedelta(days=7)

	end_date = start_date + timedelta(days=7)

	return start_date.strftime("%d/%m/%Y"), end_date.strftime("%d/%m/%Y")

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Convert OpsGenie CSV pages into ADP-friendly per-day text.",
		epilog=(
			"Examples:\n"
			"  python opsgenie_to_adp.py --csv finalAlertData.csv\n"
			"  python opsgenie_to_adp.py --zip finalAlertData.zip\n"
			"  python opsgenie_to_adp.py --zip finalAlertData.zip --tinyid 6000,6005,6012\n"
			"  python opsgenie_to_adp.py --zip finalAlertData.zip --settime 6011=1.25,6010=1:15\n"
			"  python opsgenie_to_adp.py --zip finalAlertData.zip --tinyid 6005 --settime 6005=1.75\n"
			"  python opsgenie_to_adp.py --zip finalAlertData.zip --out /tmp/adp.txt\n"
			"\n"
			"Notes:\n"
			"  --tinyid forces TinyID(s) into the payable report even if they overlap.\n"
			"  --settime accepts either HH:MM or decimal hours.\n"
			"    1:15 = 1 hour 15 minutes\n"
			"    1.25 = 1 hour 15 minutes\n"
			"    1.75 = 1 hour 45 minutes\n"
			"    1.9  = 1 hour 54 minutes\n"
			"    2    = 2 hours\n"
			"  --zip reads the CSV directly from the OpsGenie zip export.\n"
			"  Output includes:\n"
			"    1. Full Report  - all filtered alerts marked as SUBMIT / FORCED / OVERLAP\n"
			"    2. ADP Output   - payable entries only, grouped by day, with day totals\n"
		),
		formatter_class=argparse.RawTextHelpFormatter,
	)

	source_group = parser.add_mutually_exclusive_group(required=True)
	source_group.add_argument(
		"--csv",
		help="Path to OpsGenie CSV export.",
	)
	source_group.add_argument(
		"--zip",
		dest="zip_path",
		help="Path to OpsGenie zip export containing exactly one CSV.",
	)

	parser.add_argument(
		"--out",
		default=None,
		help="Optional output file path. If omitted, output only prints to console.",
	)
	parser.add_argument(
		"--owner",
		default=None,
		help="Optional owner filter. Exact match against the Owner column.",
	)
	parser.add_argument(
		"--tinyid",
		default=None,
		help="Force TinyID(s) into the payable report even if they overlap. Example: --tinyid 6000,6005,6012",
	)
	parser.add_argument(
		"--settime",
		default=None,
		help=(
			"Override payable time per TinyID. Example: --settime 6011=1.25,6010=1:15\n"
			"Accepted formats:\n"
			"  HH:MM          literal hours/minutes\n"
			"  decimal hours  1.75 = 1 hour 45 minutes\n"
			"  whole hours    2 = 2 hours"
		),
	)

	return parser.parse_args()


def parse_time_overrides(value: Optional[str]) -> dict[int, int]:
	if not value:
		return {}

	result: dict[int, int] = {}

	for part in value.split(","):
		part = part.strip()
		if not part:
			continue

		if "=" not in part:
			raise ValueError(f"Invalid --settime entry: {part}")

		tinyid_text, duration_text = part.split("=", 1)
		tiny_id = int(tinyid_text.strip())
		duration_minutes = parse_duration_override(duration_text.strip())
		result[tiny_id] = duration_minutes

	return result

def parse_time_overrides(value: Optional[str]) -> dict[int, int]:
	if not value:
		return {}

	result: dict[int, int] = {}

	for part in value.split(","):
		part = part.strip()
		if not part:
			continue

		if "=" not in part:
			raise ValueError(f"Invalid --settime entry: {part}")

		tinyid_text, duration_text = part.split("=", 1)
		tiny_id = int(tinyid_text.strip())
		duration_minutes = parse_duration_override(duration_text.strip())
		result[tiny_id] = duration_minutes

	return result


def parse_duration_override(text: str) -> int:
	value = text.strip()

	if ":" in value:
		parts = value.split(":")
		if len(parts) != 2:
			raise ValueError(f"Invalid HH:MM duration format: {text}")

		hours = int(parts[0])
		minutes = int(parts[1])

		if hours < 0 or minutes < 0 or minutes >= 60:
			raise ValueError(f"Invalid HH:MM duration value: {text}")

		total = (hours * 60) + minutes
		return total

	if "." in value:
		hours_float = float(value)
		if hours_float < 0:
			raise ValueError(f"Invalid decimal hour value: {text}")

		total = round(hours_float * 60)
		return total

	hours = int(value)
	if hours < 0:
		raise ValueError(f"Invalid hour value: {text}")

	total = hours * 60
	return total

def parse_date_set(values: List[str]) -> Set[str]:
	result = set()
	for value in values:
		datetime.strptime(value, "%Y-%m-%d")
		result.add(value)
	return result

def parse_tinyid_list(value: Optional[str]) -> Set[int]:
	if not value:
		return set()

	result = set()
	for part in value.split(","):
		part = part.strip()
		if part:
			result.add(int(part))
	return result


def load_alerts(csv_path: Optional[Path], zip_path: Optional[Path], owner_filter: Optional[str]) -> List[Alert]:
	alerts = []

	with open_opsgenie_csv_stream(csv_path, zip_path) as f:
		reader = csv.DictReader(f)
		for row in reader:
			if owner_filter and row.get("Owner", "").strip() != owner_filter.strip():
				continue

			created_at = datetime.strptime(row["CreatedAtDate"].strip(), DT_FORMAT)
			alerts.append(
				Alert(
					tiny_id=int(row["TinyID"]),
					created_at_ms=int(row["CreatedAt"]),
					created_at=created_at,
					message=row.get("Message", "").strip(),
					owner=row.get("Owner", "").strip(),
					teams=row.get("Teams", "").strip(),
					raw=row,
				)
			)

	alerts.sort(key=lambda a: (a.created_at, a.tiny_id))
	return alerts


def is_payable(alert: Alert, include_dates: Set[str]) -> bool:
	local_date = alert.created_at.date().isoformat()
	if local_date in include_dates:
		return True

	weekday = alert.created_at.weekday()  # Mon=0 .. Sun=6
	current_time = alert.created_at.timetz().replace(tzinfo=None)

	if weekday <= 4:
		if current_time >= datetime.strptime("09:00", "%H:%M").time() and current_time < datetime.strptime("17:00", "%H:%M").time():
			return False

	return True

def format_time(dt: datetime) -> str:
	hour = dt.hour % 12
	if hour == 0:
		hour = 12
	suffix = "a.m." if dt.hour < 12 else "p.m."
	return f"{hour}:{dt.minute:02d} {suffix}"


def round_duration_minutes(total_minutes: int) -> int:
	if total_minutes <= 60:
		return 60

	extra = total_minutes - 60
	rounded_extra = ((extra + 14) // 15) * 15
	return 60 + rounded_extra


def parse_duration_to_minutes(text: str) -> int:
	value = text.strip()
	if not value:
		return 60

	parts = value.split(":")
	if len(parts) != 2:
		raise ValueError("Duration must be HH:MM")

	hours = int(parts[0])
	minutes = int(parts[1])

	if hours < 0 or minutes < 0 or minutes >= 60:
		raise ValueError("Invalid HH:MM duration")

	total = (hours * 60) + minutes
	return round_duration_minutes(total)

def render_output(blocks: List[PayBlock]) -> str:
	if not blocks:
		return "No payable blocks selected."

	grouped = defaultdict(list)
	for block in blocks:
		grouped[block.anchor.created_at.date()].append(block)

	period_start, period_end = get_oncall_window(blocks[0].anchor.created_at)

	lines = []
	lines.append(f"Period Start: {period_start}")
	lines.append(f"Period End:   {period_end}")
	lines.append("")

	for day_date in sorted(grouped.keys()):
		day_blocks = grouped[day_date]
		lines.append("------------------------")

		lines.append(f"{day_blocks[0].anchor.created_at.strftime('%a %m-%d')}")

		day_total_minutes = 0

		for block in day_blocks:
			duration_text = f"{block.duration_minutes // 60}:{block.duration_minutes % 60:02d}"
			lines.append(
				f"{block.anchor.tiny_id} - {format_time(block.anchor.created_at)} - {duration_text}: {block.anchor.message}"
			)
			day_total_minutes += block.duration_minutes

		total_text = f"{day_total_minutes // 60}:{day_total_minutes % 60:02d}"
		lines.append(f"\tDay Total: {total_text}")
		lines.append("")
	lines.append("------------------------")
	lines.append("ON CALL STANDBY WEEKLY : 1 Hour")
	lines.append("------------------------")

	return "\n".join(lines).rstrip() + "\n"

def clear_screen():
	os.system("cls" if os.name == "nt" else "clear")

def main() -> int:
	clear_screen()
	args = parse_args()
	include_dates = parse_date_set(args.include_date)
	forced_ids = parse_tinyid_list(args.tinyid)
	time_overrides = parse_time_overrides(args.settime)

	csv_path = Path(args.csv) if args.csv else None
	zip_path = Path(args.zip_path) if args.zip_path else None

	alerts = load_alerts(csv_path, zip_path, args.owner)
	alerts = [a for a in alerts if is_payable(a, include_dates)]

	if not alerts:
		print("No payable alerts found after filtering.")
		return 0

	period_start, period_end = get_oncall_window(alerts[0].created_at)
	print(f"On-call period: {period_start} -> {period_end}")

	blocks = build_default_pay_blocks(alerts, forced_ids, time_overrides, default_minutes=60)

	full_report = render_full_report(alerts, blocks, forced_ids)
	output = render_output(blocks)

	print("\n===== FULL REPORT =====\n")
	print(full_report)
	print("Use --tinyid {number} to activate overlaps in final report")

	print("\n===== ADP OUTPUT =====\n")
	print(output)
	print("Use --settime {number}={hours.percenthour} or --settime {number}={hours:minutes} to force spent time on ticket")

	if args.out:
		out_path = Path(args.out)
		out_path.write_text(output, encoding="utf-8")
		print(f"Saved output to: {out_path}")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())

