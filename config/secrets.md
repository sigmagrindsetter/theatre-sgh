# Secrets Configuration

## Overview

Universal authentication - credentials shared across all integrations.

## Required Secrets

### Local Development (`.env`)

```bash
NOTION_API_TOKEN=your_notion_token
GOOGLE_CLIENT_EMAIL=your_service_account_email
GOOGLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."
```

### GitHub Actions

Add in Settings → Secrets and variables → Actions:

1. **NOTION_API_TOKEN** - Notion integration token
2. **GOOGLE_CLIENT_EMAIL** - Service account email
3. **GOOGLE_PRIVATE_KEY** - Full private key (including BEGIN/END lines)

## What's NOT Secret

Stored in code (each app's `config.py`):
- Google Sheet IDs
- Notion Database IDs

## Setup

### Google Sheets Access
1. Share sheet with service account email
2. Give "Viewer" permissions

### Notion Access
1. Go to Notion database
2. Click "..." → "Add connections"
3. Select your integration

## Adding New Integrations

New integrations inherit authentication automatically:
1. Share Google Sheet with service account
2. Share Notion database with integration
3. Add IDs to app's `config.py`

No new secrets required.
