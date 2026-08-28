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
			elif not is_payable(alert, include_dates=set()):
				color = OVERLAP_COLOR
				status = "EXCLUDE"
			elif alert.tiny_id in submit_ids:
				color = SUBMIT_COLOR
				status = "SUBMIT "
			else:
				color = OVERLAP_COLOR
				status = "OVERLAP"

			reason = " - business hours, not payable" if status == "EXCLUDE" else ""
			lines.append(
				f"{color}{status} | {alert.tiny_id} - {format_time(alert.created_at)}: {alert.message}{reason}{RESET_COLOR}"
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


def get_oncall_window_multi(reference_dt: datetime, last_dt: datetime) -> tuple[str, str]:
	"""Calculate the on-call period spanning from the Friday of or before the first alert
	to the Friday of or after the last alert."""

	# Start: Friday of or before the first alert
	days_back = (reference_dt.weekday() - 4) % 7
	start_date = (reference_dt - timedelta(days=days_back)).date()

	if reference_dt.weekday() == 4 and reference_dt.time() < time(17, 0):
		start_date = start_date - timedelta(days=7)

	# End: Friday of or after the last alert
	days_forward = (4 - last_dt.weekday()) % 7
	end_date = (last_dt + timedelta(days=days_forward)).date()

	return start_date.strftime("%d/%m/%Y"), end_date.strftime("%d/%m/%Y")

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Convert OpsGenie CSV pages into ADP-friendly per-day text.",
		epilog=(
			"Examples:\n"
			"  python readcsv.py --csv finalAlertData.csv\n"
			"  python readcsv.py --zip finalAlertData.zip\n"
			"  python readcsv.py --auto\n"
			"  python readcsv.py --auto --tinyid 6000,6005,6012\n"
			"  python readcsv.py --auto --settime 6011=1.25,6010=1:15\n"
			"  python readcsv.py --auto --out /tmp/adp.txt\n"
			"  python readcsv.py --auto --cleanup\n"
			"\n"
			"Notes:\n"
			"  --tinyid forces TinyID(s) into the payable report even if they overlap.\n"
			"           tinyid is OpsGenies 'Primary Key' for this queue\n"
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

	source_group = parser.add_mutually_exclusive_group(required=False)
	source_group.add_argument(
		"--csv",
		help="Path to OpsGenie CSV export.",
	)
	source_group.add_argument(
		"--zip",
		dest="zip_path",
		help="Path to OpsGenie zip export containing exactly one CSV.",
	)
	source_group.add_argument(
		"--auto",
		action="store_true",
		help="Auto-detect the youngest alert-export-result_*.zip in the current directory and process it.",
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

	parser.add_argument(
		"--cleanup",
		action="store_true",
		help="Delete all but the youngest alert-export-result_*.zip file.",
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

def render_output(blocks: List[PayBlock], all_alerts: List[Alert]) -> str:
	if not blocks:
		return "No payable blocks selected."

	grouped = defaultdict(list)
	for block in blocks:
		grouped[block.anchor.created_at.date()].append(block)

	first_alert = min(all_alerts, key=lambda a: a.created_at)
	last_alert = max(all_alerts, key=lambda a: a.created_at)
	period_start, period_end = get_oncall_window_multi(first_alert.created_at, last_alert.created_at)

	lines = []
	lines.append(f"Period Start: {period_start}")
	lines.append(f"Period End:   {period_end}")
	lines.append("")

	first_date = min(alert.created_at.date() for alert in all_alerts)
	last_date = max(alert.created_at.date() for alert in all_alerts)
	while last_date.weekday() != 4:
		last_date += timedelta(days=1)

	all_days = set(grouped.keys())
	current_date = first_date
	first_friday_seen = False
	standby_fridays = set()
	while current_date <= last_date:
		if current_date.weekday() == 4:
			if first_friday_seen:
				standby_fridays.add(current_date)
			else:
				first_friday_seen = True
		current_date += timedelta(days=1)

	all_days.update(standby_fridays)

	for day_date in sorted(all_days):
		day_blocks = grouped.get(day_date, [])
		lines.append("------------------------")

		lines.append(f"{day_date.strftime('%a %m-%d')}")

		day_total_minutes = 0

		for block in day_blocks:
			duration_text = f"{block.duration_minutes // 60}:{block.duration_minutes % 60:02d}"
			lines.append(
				f"{block.anchor.tiny_id} - {format_time(block.anchor.created_at)} - {duration_text}: {block.anchor.message}"
			)
			day_total_minutes += block.duration_minutes

		if day_date in standby_fridays:
			lines.append("ON CALL STANDBY WEEKLY : 1 Hour")
			day_total_minutes += 60

		total_text = f"{day_total_minutes // 60}:{day_total_minutes % 60:02d}"
		lines.append(f"\tDay Total: {total_text}")
		lines.append("")
	lines.append("------------------------")

	return "\n".join(lines).rstrip() + "\n"

def clear_screen():
	os.system("cls" if os.name == "nt" else "clear")

def find_youngest_auto_zip() -> Optional[Path]:
	"""Find the youngest alert-export-result_*.zip in the current directory or downloads/ subdirectory."""
	candidates: list[Path] = []

	# Check current directory first
	for z in Path.cwd().glob("alert-export-result_*.zip"):
		candidates.append(z)

	# Then check downloads/ subdirectory
	downloads = Path.cwd() / "downloads"
	if downloads.is_dir():
		for z in downloads.glob("alert-export-result_*.zip"):
			candidates.append(z)

	if not candidates:
		return None

	youngest = max(candidates, key=lambda p: p.stat().st_mtime)
	return youngest


def cleanup_old_zips() -> list[Path]:
	"""Delete all but the youngest alert-export-result_*.zip file. Returns the list of deleted files."""
	candidates: list[Path] = []

	# Check current directory first
	for z in Path.cwd().glob("alert-export-result_*.zip"):
		candidates.append(z)

	# Then check downloads/ subdirectory
	downloads = Path.cwd() / "downloads"
	if downloads.is_dir():
		for z in downloads.glob("alert-export-result_*.zip"):
			candidates.append(z)

	# Nothing to do
	if len(candidates) <= 1:
		return []

	# Sort by modification time, youngest first
	candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
	youngest = candidates[0]

	# Delete all except the youngest
	deleted: list[Path] = []
	for old_zip in candidates[1:]:
		old_zip.unlink()
		deleted.append(old_zip)

	return deleted

def main() -> int:
	clear_screen()
	args = parse_args()
	forced_ids = parse_tinyid_list(args.tinyid)
	time_overrides = parse_time_overrides(args.settime)

	csv_path = Path(args.csv) if args.csv else None
	zip_path = Path(args.zip_path) if args.zip_path else None

	if args.auto:
		zip_path = find_youngest_auto_zip()
		if zip_path is None:
			print("Cannot find the latest alert-export-result_*.zip file")
			return 1
		print(f"Auto-detected: {zip_path}")

	deleted_zips: list[Path] = []
	if args.cleanup:
		deleted_zips = cleanup_old_zips()

	if not csv_path and not zip_path:
		if deleted_zips:
			print("\n===== CLEANUP =====")
			for z in deleted_zips:
				print(f"Deleted: {z}")
		return 0

	alerts = load_alerts(csv_path, zip_path, args.owner)
	payable_alerts = [
		alert for alert in alerts
		if is_payable(alert, include_dates=set()) or alert.tiny_id in forced_ids
	]

	if not alerts:
		print("No alerts found after filtering.")
		return 0

	first_alert = min(alerts, key=lambda a: a.created_at)
	last_alert = max(alerts, key=lambda a: a.created_at)
	period_start, period_end = get_oncall_window_multi(first_alert.created_at, last_alert.created_at)
	print(f"On-call period: {period_start} -> {period_end}")

	blocks = build_default_pay_blocks(payable_alerts, forced_ids, time_overrides, default_minutes=60)

	full_report = render_full_report(alerts, blocks, forced_ids)
	output = render_output(blocks, alerts)

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

	if deleted_zips:
		print("\n===== CLEANUP =====")
		for z in deleted_zips:
			print(f"Deleted: {z}")

	# Suggest --cleanup if there are old zips and --cleanup wasn't used
	if not args.cleanup and args.auto:
		candidates: list[Path] = []
		for z in Path.cwd().glob("alert-export-result_*.zip"):
			candidates.append(z)
		downloads = Path.cwd() / "downloads"
		if downloads.is_dir():
			for z in downloads.glob("alert-export-result_*.zip"):
				candidates.append(z)
		if len(candidates) > 1:
			candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
			youngest = candidates[0]
			now = datetime.now()
			print("\nOld alert-export-result_*.zip files:")
			for z in candidates[1:]:
				mtime = datetime.fromtimestamp(z.stat().st_mtime)
				age_days = (now - mtime).days
				age_str = f" ({age_days} days old)" if age_days > 0 else ""
				print(f"  {z.name}  [{mtime.strftime('%Y-%m-%d %H:%M:%S')}]  {age_str}")
			print("Run with --cleanup to remove old reports.")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())

