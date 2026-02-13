# Imunify Demo Recording Mover

Automates the workflow you described:

- Reads the latest Gemini email that contains multiple “Recording” links
- Creates the target folder on Google Drive:
  - `Imunify/Demo/<YYYY>/Imunify360 Product Demo <MM>.<YY>`
- Lets you enter the order of recordings (e.g. `3,1,2`)
- Moves the recordings into the created folder in that order
- Renames the recordings based on the last N rows of the Slite “Demos” table
- Makes each recording link public and writes the public links back into Slite’s “Recording” column

## Setup

1) Install Python 3.10+.

2) Install dependencies:

```bash
python -m pip install -r requirements.txt
python -m playwright install
```

3) Create a Google Cloud “Desktop app” OAuth client and download `credentials.json`.

- Enable APIs:
  - Gmail API
  - Google Drive API

Place `credentials.json` next to `main.py` (project root).

4) Create `.env` from the example:

```bash
copy .env.example .env
```

Fill `SLITE_TABLE_URL`.

## Run

```bash
python main.py
```

On first run, it will open a browser for Google OAuth.
For Slite, it will open an automated browser session; you may need to login once.

