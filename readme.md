# OpsGenie → ADP Converter

A small CLI utility to convert OpsGenie alert exports into a clean report suitable for manual entry into ADP.

The tool reads an OpsGenie CSV export, or the downloaded zip containing it, filters alerts that occur during normal work hours, groups overlapping pages into payable blocks, and produces two reports:

1. Full Report — shows every alert with status markers
2. ADP Output — only the entries that should be submitted for pay

This replaces a manual workflow that involved exporting CSV data, manipulating it in Excel or Sheets, and manually reconciling overlapping alerts.

## Features

- Reads OpsGenie CSV exports directly
- Can read OpsGenie zip downloads without manual extraction
- Automatically removes alerts during Mon–Fri 09:00–17:00
- Groups alerts into payable blocks
- Supports manual overrides via CLI
- Shows overlap vs payable alerts
- Calculates per-day totals
- Outputs clean copy/paste text for ADP
- Displays the on-call pay period window

## Requirements

- Python 3.9 or newer
- No external dependencies required

## Installation

Clone the repository:

```
git clone https://github.com/Pontiac76/OpsGenie2ADP.git
cd Opsgenie2ADP
```

No package install is needed.

## Usage

### Preparation
- Log into OpsGenie and go to the Alerts tab.
- Set the alerts search field to `status: open AND owner: me`
- Change the filter to "Last Month"
- Click the icon to the right of "Last Month" and select "Export CSV"
- Check your email and you will be provided a link to download the CSV when the report is done.
- You'll immediately then download the CSV.  Extract it somewhere, or just refer to the ZIP file itself with the use of this script.

### Basic run

Python is the interpreter.  You may need to use either just python or python3.  Depends on how your environment is setup.

At minimum:
```
python readcsv.py --csv finalAlertData.csv
```

### Using the OpsGenie zip export

OpsGenie exports alerts as a zip containing a CSV. This tool can read the zip directly.

```
python readcsv.py --zip finalAlertData.zip
```

## CLI Options

### --csv

Path to the OpsGenie CSV export.

Example:

```
python readcsv.py --csv finalAlertData.csv
```

### --zip

Path to the OpsGenie zip export.

The zip must contain exactly one CSV.

Example:

```
python readcsv.py --zip finalAlertData.zip
```

### --tinyid

Force specific alerts to appear in the final ADP output even if they overlap with another block.

Example:

```
python readcsv.py --csv finalAlertData.csv --tinyid 6000,6005,6012
```

### --settime

Override payable duration for specific TinyIDs.

Format:

```
TinyID=duration
```

Multiple overrides are comma separated.

Example:

```
python readcsv.py --csv finalAlertData.csv --settime 6011=1.25,6010=1:15
```

Supported duration formats:

| Format | Meaning |
|---|---|
| 1:15 | 1 hour 15 minutes |
| 1.25 | decimal hours, 75 minutes |
| 1.75 | decimal hours, 1 hour 45 minutes |
| 1.9 | decimal hours, about 1 hour 54 minutes |
| 2 | 2 hours |

### --include-date

Force a specific date to be considered payable even if it occurs during weekday working hours.

Example:

```
python readcsv.py --csv finalAlertData.csv --include-date 2026-01-01
```

Multiple dates are allowed:

```
python readcsv.py --csv finalAlertData.csv --include-date 2026-01-01 --include-date 2026-01-15
```

### --owner

Filter alerts by the Owner field.

Example:

```
python readcsv.py --csv finalAlertData.csv --owner Stephen
```

### --out

Write the ADP report to a file.

Linux example:

```
python readcsv.py --csv finalAlertData.csv --out /tmp/adp.txt
```

Windows PowerShell example:

```
python .\readcsv.py --csv .\finalAlertData.csv --out $env:TEMP\adp.txt
```

If omitted, output only appears in the terminal.

## Example Runs

### Basic CSV run

```
python readcsv.py --csv finalAlertData.csv
```

### Basic zip run

```
python readcsv.py --zip finalAlertData.zip
```

### Force a TinyID and override time

```
python readcsv.py --zip finalAlertData.zip --tinyid 6005 --settime 6005=1.75
```

### More complex example

```
python readcsv.py \
  --zip finalAlertData.zip \
  --tinyid 6005,6012 \
  --settime 6005=1.75,6012=3 \
  --include-date 2026-01-01 \
  --out /tmp/adp.txt
```

## Output

The tool generates two reports.

### Full Report

Shows all alerts and their classification.

```
SUBMIT  | 6005 - 5:36 a.m.: NAVBLUE Ticket NIL Flights
OVERLAP | 6006 - 5:42 a.m.: NAVBLUE Ticket NIL Flights
FORCED  | 6012 - 9:08 p.m.: MSYS Queue Alert
```

Status meanings:

| Status | Meaning |
|---|---|
| SUBMIT | Anchor alert for a payable block |
| FORCED | Explicitly forced via --tinyid |
| OVERLAP | Alert occurred during another block |

### ADP Output

Clean output intended for copy/paste into ADP.

```
Period Start: 14 March 2026
Period End: 21 March 2026

Sunday - 15 March 2026
6005 - 5:36 a.m. - 1:45: Ticket NIL Information
Day Total: 1:45

Tuesday - 17 March 2026
6012 - 9:08 p.m. - 3:00: MSYS Queues
Day Total: 3:00
```

## Pay Period Calculation

The on-call window is automatically calculated as:

```
Friday 17:00 → next Friday 17:00 Eastern Time
```

The script determines the correct period based on the first alert timestamp.

## Workflow

Typical process:

1. Download alert export from OpsGenie
2. Run this tool on the zip or CSV
3. Review Full Report
4. Copy ADP Output
5. Paste into ADP timesheet

## Notes

- Default payable block is 1 hour
- Additional pages inside that window are automatically absorbed
- Absorbed pages do not appear in ADP output
- Per-TinyID overrides extend the absorption window automatically
- Day totals are calculated automatically

## License

Personal utility script. Use or modify as needed.

# Sample Output
```
On-call period: 06/03/2026 -> 13/03/2026

===== FULL REPORT =====

Friday - 06 March 2026
SUBMIT  | 5999 - 6:54 p.m.: INC-126890--Service:emaild locked
OVERLAP | 6000 - 7:33 p.m.: Fwd: Ticket Plans not received from Mar6-2026 1.10PM to 2.02PM HST

Sunday - 08 March 2026
SUBMIT  | 6004 - 1:11 a.m.: Fwd:  Ticket Software not working
OVERLAP | 6005 - 1:54 a.m.: INC-126950-- Nagios is Down
SUBMIT  | 6006 - 6:01 a.m.: INC-126962-- MSYS Queues -- Oddball System
SUBMIT  | 6007 - 9:53 a.m.: INC-126967 Comm Server MSYS Queues is CRITICAL
SUBMIT  | 6008 - 3:39 p.m.: Fwd: Ticket Unknown error
SUBMIT  | 6010 - 4:40 p.m.: INC-126981 -- Disk Usage - Linux
SUBMIT  | 6011 - 7:28 p.m.: Fwd: Ticket CAN NOT PULL THE I3 Files
OVERLAP | 6012 - 7:33 p.m.: INC-126991 - MSYS Queue Backup - A very important system

Use --tinyid {number} to activate overlaps in final report

===== ADP OUTPUT =====

Period Start: 06/03/2026
Period End:   13/03/2026

------------------------
Fri 03-06
5999 - 6:54 p.m. - 1:00: INC-126890--Service:emaild locked
        Day Total: 1:00

------------------------
Sun 03-08
6004 - 1:11 a.m. - 1:00: Fwd: Ticket Software not working
6006 - 6:01 a.m. - 1:00: INC-126962-- MSYS Queues -- Oddball System
6007 - 9:53 a.m. - 1:00: INC-126967 Comm Server MSYS Queues is CRITICAL
6008 - 3:39 p.m. - 1:00: Fwd: Ticket Unknown error
6010 - 4:40 p.m. - 1:00: INC-126981 -- Disk Usage - Linux
6011 - 7:28 p.m. - 2:00: Fwd: Ticket CAN NOT PULL THE I3 Files
        Day Total: 7:00

------------------------
ON CALL STANDBY WEEKLY : 1 Hour
------------------------

Use --settime {number}={hours.percenthour} or --settime {number}={hours:minutes} to force spent time on ticket
```