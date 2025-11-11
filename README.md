# Theatre SGH Control Panel

Private repository for theatre management - apps, integrations and automations with Notion, Google Drive and GitHub Actions.

## Structure

```
theatre-sgh/
├── shared/            # Shared utilities
│   ├── auth.py       # Universal Google & Notion authentication
│   └── sync.py       # Base sync class
├── apps/              # Individual automation apps
│   └── recruitment/   # Google Sheets → Notion sync
│       ├── config.py  # Sheet/Database IDs
│       ├── sync.py    # Sync implementation
│       └── README.md
├── .github/workflows/ # GitHub Actions
└── requirements.txt   # Python dependencies
```

## Architecture

### Universal Authentication
All apps share authentication via `shared/auth.py`:
- **Google Sheets**: Service account credentials from environment variables
- **Notion**: API token from environment variables
- Singleton pattern - credentials loaded once, reused everywhere

### Base Sync Service
All integrations inherit from `BaseSyncService` in `shared/sync.py`:
- Handles authentication automatically
- Provides common sync logic
- Each app implements `transform_record()` method

### Configuration
- **Secrets** (`.env` and GitHub Secrets):
  - `NOTION_API_TOKEN`
  - `GOOGLE_CLIENT_EMAIL`
  - `GOOGLE_PRIVATE_KEY`
- **IDs** (in code):
  - Google Sheet IDs
  - Notion Database IDs

## Current Apps

### Recruitment
Syncs recruitment records from Google Sheets to Notion every hour.

- [Google Sheet](https://docs.google.com/spreadsheets/d/1kBhtnYQNuuunlXxdDehMl85Pg6Q215W7w03qmaXCPGM)
- [Notion Database](https://www.notion.so/2893f4160a378020af8fcbb023071263)
- [Details](apps/recruitment/README.md)

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure `.env`:
   ```bash
   NOTION_API_TOKEN=your_token
   GOOGLE_CLIENT_EMAIL=your_email
   GOOGLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."
   ```

3. Run:
   ```bash
   python apps/recruitment/sync.py
   ```

## GitHub Actions

### Secrets Required
Add in Settings → Secrets and variables → Actions:
- `NOTION_API_TOKEN`
- `GOOGLE_CLIENT_EMAIL`
- `GOOGLE_PRIVATE_KEY`

### Workflows
- **Recruitment Sync**: Automatic (hourly) + Manual trigger

## Adding New Integrations

1. Create app directory:
   ```bash
   mkdir apps/new_app
   ```

2. Create `config.py`:
   ```python
   SHEET_ID = "your-sheet-id"
   DATABASE_ID = "your-database-id"
   SHEET_NAME = None
   ```

3. Create `sync.py`:
   ```python
   #!/usr/bin/env python3
   import sys
   from pathlib import Path

   project_root = Path(__file__).parent.parent.parent
   sys.path.insert(0, str(project_root))

   from shared import BaseSyncService
   from config import SHEET_ID, DATABASE_ID, SHEET_NAME

   class NewAppSync(BaseSyncService):
       SHEET_ID = SHEET_ID
       DATABASE_ID = DATABASE_ID
       SHEET_NAME = SHEET_NAME

       def transform_record(self, record):
           return {
               "Name": {"title": [{"text": {"content": record["Name"]}}]},
               # Add more mappings
           }

   if __name__ == "__main__":
       sync = NewAppSync()
       success = sync.run()
       sys.exit(0 if success else 1)
   ```

4. Create GitHub Actions workflow (`.github/workflows/new-app-sync.yml`):
   ```yaml
   name: New App Sync
   on:
     schedule:
       - cron: '0 * * * *'
     workflow_dispatch:
   jobs:
     sync:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: '3.11'
             cache: 'pip'
         - run: pip install -r requirements.txt
         - run: python apps/new_app/sync.py
           env:
             NOTION_API_TOKEN: ${{ secrets.NOTION_API_TOKEN }}
             GOOGLE_CLIENT_EMAIL: ${{ secrets.GOOGLE_CLIENT_EMAIL }}
             GOOGLE_PRIVATE_KEY: ${{ secrets.GOOGLE_PRIVATE_KEY }}
   ```

No new secrets needed - authentication is universal.
