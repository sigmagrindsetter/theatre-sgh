# Recruitment Sync

Syncs recruitment records from Google Sheets to Notion database every hour.

## Column Mapping

Google Sheets → Notion:
- `Imię i nazwisko:` → `Imię i nazwisko` (Title)
- `Sygnatura czasowa` → `Czas` (Text)
- `Adres e-mail:` → `Email` (Email)
- `Na jakiej uczelni studiujesz?` → `Uczelnia` (Text)
- `Na którym roku studiów jesteś?` → `Rok studiów` (Text)
- `W jakich obszarach chcesz rozwijać się w naszej organizacji?` → `Obszary` (Text)
- `Tu wstaw link do swojego filmiku rekrutacyjnego` → `Filmik` (URL)

## Configuration

- Google Sheet: https://docs.google.com/spreadsheets/d/1kBhtnYQNuuunlXxdDehMl85Pg6Q215W7w03qmaXCPGM
- Notion Database: https://www.notion.so/2893f4160a378020af8fcbb023071263
- Config: `config.py`

## Usage

### Run Locally
```bash
python apps/recruitment/sync.py
```

### GitHub Actions
- **Automatic**: Runs every hour
- **Manual**: Actions → "Recruitment Sync" → "Run workflow"

## Troubleshooting

- Verify Google Sheet is shared with: `integrator@theatre-sgh.iam.gserviceaccount.com`
- Check Notion database is shared with integration
- Ensure column names match exactly (including Polish characters and colons)
