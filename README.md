# Imunify360 Demo Recordings Pipeline

A Python pipeline that fetches demo recording attachments from a Gemini email (Gmail), optionally renames them using feature names from a Slite "Demos" table, and copies or uploads them to a Google Drive demo folder.

## Overview

The workflow supports:

1. **Email → attachments** — Find the newest Gmail message with subject `Notes: "Imunify360 Product Demo"` and collect attachment names and Drive links (from MIME or from links in the HTML body).
2. **Drive operations** — Copy those recording files to a demo folder on Google Drive, or download them locally.
3. **Slite integration** — Open the Slite "Demos" table and get the last *n* values from the "Feature" column (*n* = number of recordings in the local folder).
4. **Rename & upload** — Rename local recording files with those feature names (by file order), then upload them to the Drive demo folder.

The Drive demo folder is created at:

`My Drive/Imunify/Demo/<YYYY>/Imunify360 Product Demo <mm.dd>`

(using the current date for year and month.day).

## Project structure

| File | Description |
|------|-------------|
| `main.py` | Entry point: ensures the demo folder exists on Drive and prints its path. |
| `folder_create_or_check.py` | Creates or finds the demo folder on Google Drive and exposes `get_drive_service()`, `DEMO_FOLDER_ID`, `DEMO_FOLDER_PATH`. |
| `fetch_gemini_demo_videos.py` | Fetches the latest Gemini email (subject `Notes: "Imunify360 Product Demo"`) and returns attachment names and Drive links. |
| `copy_recs_to_drive.py` | Copies recording attachments to Drive, downloads them locally, renames local files with Slite feature names, and uploads local recordings to Drive. |
| `get_names_from_slite.py` | Opens the Slite Demos table in a browser (Playwright), extracts the "Feature" column, and returns the last *n* feature names (*n* = number of files in the recordings folder). |
| `feature_names.txt` | Optional: list of feature names (one per line), e.g. from a previous Slite run. |
| `.env` / `.env.example` | Environment variables (Gmail query, Drive path, Slite URL). Copy `.env.example` to `.env` and fill as needed. |
| `requirements.txt` | Python dependencies. |

## Prerequisites

- **Python 3.10+** (uses `list[str]` type hints)
- **Google Cloud project** with Gmail API and Drive API enabled, and OAuth 2.0 credentials (Desktop app) saved as `credentials.json` in the project root
- **Chrome** (for Playwright) — used by `get_names_from_slite.py` to open the Slite Demos page

## Installation

1. Clone or copy the project and open a terminal in the project directory.

2. Create a virtual environment (recommended):

   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # Linux/macOS
   ```

3. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Install Playwright browsers (for Slite scraping):

   ```bash
   playwright install chromium
   ```

5. Add `credentials.json` (Google OAuth 2.0 client secrets) to the project root. On first run, the scripts will open a browser for login and save `token.json`.

6. Copy `.env.example` to `.env` and adjust if needed (Gmail query, Drive path, Slite table URL).

## Configuration

- **`.env`** (optional):
  - `GMAIL_QUERY` — Gmail search query (defaults in code: subject `Notes: "Imunify360 Product Demo"`).
  - `DRIVE_BASE_PATH` — Base path in "My Drive" (e.g. `Imunify/Demo`). The script builds `Imunify/Demo/<YYYY>/Imunify360 Product Demo <mm.dd>`.
  - `SLITE_TABLE_URL` — Full URL to the Slite "Demos" table page (e.g. `https://cloudlinux.slite.com/app/docs/.../Demos`).

- **Local paths** (hardcoded in the scripts; change if needed):
  - In `copy_recs_to_drive.py`: `RECORDINGS_LOCAL_FOLDER` — folder where recordings are downloaded and from which they are uploaded.
  - In `get_names_from_slite.py`: `RECORDINGS_FOLDER` — same folder, used to count files and determine *n* for "last n" feature names.

## Usage

### Ensure demo folder exists

```bash
python main.py
```

Prints the demo folder path (e.g. `My Drive/Imunify/Demo/2026/Imunify360 Product Demo 02.15`).

### List attachments from Gemini email

```bash
python fetch_gemini_demo_videos.py
```

Prints attachment names and Drive links from the latest matching email.

### Copy recordings to Drive (from email links)

From the Gemini email attachment links, copy each file into the demo folder (no local download):

```python
from copy_recs_to_drive import copy_recording_attachments_to_drive
copied = copy_recording_attachments_to_drive()
```

### Download recordings to a local folder

```python
from copy_recs_to_drive import download_recordings_to_folder
saved = download_recordings_to_folder()  # uses RECORDINGS_LOCAL_FOLDER
# Or: download_recordings_to_folder(r"C:\path\to\folder")
```

### Get feature names from Slite

Opens Chrome, loads the Slite Demos table, and extracts the last *n* feature names (*n* = number of files in the recordings folder). You may need to log in to Slite in the browser when prompted.

```bash
python get_names_from_slite.py
```

Prints the list and writes it to `feature_names.txt`.

### Rename local recordings with Slite feature names

```python
from copy_recs_to_drive import rename_local_recordings_with_feature_names
renamed = rename_local_recordings_with_feature_names()
```

Files in the local folder are ordered by modification time (oldest first) and renamed to the corresponding feature names.

### Upload local recordings to Drive (with Slite renaming)

Downloads are not done here; this assumes the local folder already has the recording files. Renames them using Slite feature names, then uploads to the demo folder:

```bash
python copy_recs_to_drive.py
```

Or programmatically:

```python
from copy_recs_to_drive import upload_local_recordings_to_drive
uploaded = upload_local_recordings_to_drive()
```

## Dependencies

- **google-api-python-client**, **google-auth**, **google-auth-httplib2**, **google-auth-oauthlib** — Gmail and Drive API
- **beautifulsoup4** — Parse HTML email body for Drive links
- **python-dotenv** — Load `.env`
- **pandas** — Optional; used in `get_names_from_slite` for table handling (fallback without pandas is built-in)
- **playwright** — Browser automation for Slite Demos table

## Notes

- The first time you use Gmail/Drive, the app will open a browser for OAuth; after that, `token.json` is reused until it expires.
- Slite scraping uses a persistent Chrome profile (`.slite_chrome_profile`) so you can log in once.
- Recording names are matched by containing the word `"recording"` (case-insensitive); if none match, all linked attachments are used.
- Drive links can be Google redirect URLs; the code resolves them and extracts the Drive file ID for copy/download.
