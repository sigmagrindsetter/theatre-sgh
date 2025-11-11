#!/usr/bin/env python3
"""
Recruitment: Google Sheets to Notion sync
Syncs recruitment records from Google Sheets to Notion database
Creates evaluator-candidate combinations for each active evaluator
"""

import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared import BaseSyncService
from config import SHEET_ID, DATABASE_ID, SHEET_NAME, EVALUATORS_DATABASE_ID


class RecruitmentSync(BaseSyncService):
    """Recruitment-specific sync implementation"""

    # IDs stored in config.py (not secrets!)
    SHEET_ID = SHEET_ID
    DATABASE_ID = DATABASE_ID
    SHEET_NAME = SHEET_NAME

    # Duplicate detection settings
    UNIQUE_KEY = "Email"  # Notion column name to check for duplicates
    UNIQUE_KEY_SHEET = "Adres e-mail:"  # Corresponding Google Sheets column name

    # Column mapping: Google Sheets -> Notion
    # Note: Trailing spaces in sheet column names are important!
    COLUMN_MAPPING = {
        "Imię i nazwisko:": "Imię i nazwisko",
        "Sygnatura czasowa": "Czas",
        "Adres e-mail:": "Email",
        "Na jakiej uczelni studiujesz?": "Uczelnia",
        "Na którym roku studiów jesteś?": "Rok studiów",
        "W jakich obszarach chcesz rozwijać się w naszej organizacji? ": "Obszary",  # Trailing space!
        "Tu wstaw link do swojego filmiku rekrutacyjnego ": "Filmik"  # Trailing space!
    }

    def get_active_evaluators(self):
        """
        Fetch list of active evaluators from Notion database
        Returns list of evaluator names where Aktywny = "Tak"
        """
        print(f"\nFetching active evaluators from database...")

        try:
            # Query the evaluators database with filter for Aktywny = "tak"
            response = self.notion_client.databases.query(
                database_id=EVALUATORS_DATABASE_ID,
                filter={
                    "property": "Aktywny",
                    "select": {
                        "equals": "tak"
                    }
                }
            )

            evaluators = []
            for page in response.get('results', []):
                props = page['properties']
                person_prop = props.get('Person', {})

                # Extract person objects (with ID and name) from Person property type
                if person_prop.get('people') and person_prop['people']:
                    # Person type returns a list of people objects
                    for person in person_prop['people']:
                        name = person.get('name')
                        person_id = person.get('id')
                        if name and person_id:
                            evaluators.append({
                                'name': name,
                                'id': person_id
                            })

            names = [e['name'] for e in evaluators]
            print(f"✓ Found {len(evaluators)} active evaluators: {', '.join(names)}")
            return evaluators

        except Exception as e:
            print(f"✗ Error fetching evaluators: {e}")
            return []

    def get_existing_records(self):
        """
        Override to get existing evaluator-candidate pairs
        Returns dict of {(candidate_name, evaluator_name): page_id}
        Handles pagination to fetch ALL records
        """
        print(f"Checking existing evaluator-candidate pairs in Notion...")

        try:
            existing = {}
            has_more = True
            next_cursor = None

            # Fetch ALL records with pagination
            while has_more:
                query_params = {"database_id": self.DATABASE_ID}
                if next_cursor:
                    query_params["start_cursor"] = next_cursor

                response = self.notion_client.databases.query(**query_params)

                for page in response.get('results', []):
                    props = page['properties']

                    # Get candidate name (Imię i nazwisko)
                    name_prop = props.get('Imię i nazwisko', {})
                    candidate_name = None
                    if name_prop.get('title') and name_prop['title']:
                        candidate_name = name_prop['title'][0]['text']['content']

                    # Get evaluator (Oceniający - Person type)
                    evaluator_prop = props.get('Oceniający', {})
                    evaluator = None
                    if evaluator_prop.get('people') and evaluator_prop['people']:
                        # Get first person's name
                        evaluator = evaluator_prop['people'][0].get('name')

                    if candidate_name and evaluator:
                        existing[(candidate_name, evaluator)] = page['id']

                has_more = response.get('has_more', False)
                next_cursor = response.get('next_cursor')

            print(f"✓ Found {len(existing)} existing evaluator-candidate pairs")
            return existing

        except Exception as e:
            print(f"⚠ Could not fetch existing records: {e}")
            return {}

    def parse_timestamp(self, timestamp_str):
        """
        Parse timestamp string to ISO format for Notion date property
        Handles format: "2025-10-11 14:58:44" or similar
        Returns ISO datetime string with timezone
        """
        try:
            # Try common formats
            for fmt in ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"]:
                try:
                    dt = datetime.strptime(timestamp_str.strip(), fmt)
                    # Return ISO format with time
                    return dt.isoformat()
                except ValueError:
                    continue

            # If no format works, return None
            return None
        except Exception:
            return None

    def transform_record(self, record, evaluator=None):
        """
        Transform Google Sheet record to Notion properties
        Uses proper column mapping and adds evaluator if provided
        """
        properties = {}

        for sheet_col, notion_col in self.COLUMN_MAPPING.items():
            # Check if column exists in record
            if sheet_col not in record:
                # Try without trailing/leading spaces
                found = False
                for key in record.keys():
                    if key.strip() == sheet_col.strip():
                        sheet_col = key
                        found = True
                        break

                if not found:
                    continue

            value = record[sheet_col]

            # Skip completely empty values
            if not value or (isinstance(value, str) and not value.strip()):
                continue

            value = str(value).strip()

            # Determine property type based on Notion column name
            if notion_col == "Imię i nazwisko":
                # Title property (required for Notion pages)
                properties[notion_col] = {
                    "title": [{"text": {"content": value}}]
                }
            elif notion_col == "Czas":
                # Date property with time
                date_iso = self.parse_timestamp(value)
                if date_iso:
                    properties[notion_col] = {
                        "date": {"start": date_iso}
                    }
                else:
                    # Fallback: keep original value as date string
                    properties[notion_col] = {
                        "date": {"start": value[:10]}  # Take just YYYY-MM-DD part
                    }
            elif notion_col == "Email":
                # Email property
                properties[notion_col] = {
                    "email": value
                }
            elif notion_col == "Filmik":
                # URL property
                if value.startswith("http"):
                    properties[notion_col] = {"url": value}
            else:
                # Rich text for everything else (Uczelnia, Rok studiów, Obszary)
                properties[notion_col] = {
                    "rich_text": [{"text": {"content": value}}]
                }

        # Add evaluator column (Person type)
        if evaluator:
            properties["Oceniający"] = {
                "people": [{"id": evaluator['id']}]
            }

        return properties

    def sync_to_notion(self, records):
        """
        Override sync to create evaluator-candidate combinations
        """
        print(f"\nSyncing {len(records)} candidates to Notion...")

        # Get active evaluators
        evaluators = self.get_active_evaluators()

        if not evaluators:
            print("⚠ No active evaluators found. Skipping sync.")
            return 0, 0, 0

        # Get existing pairs
        existing_pairs = self.get_existing_records()

        created_count = 0
        skipped_count = 0
        error_count = 0

        # For each candidate, create a record for each evaluator
        for record in records:
            candidate_name_raw = record.get("Imię i nazwisko:", "Unknown")
            if not candidate_name_raw or candidate_name_raw == "Unknown":
                print(f"  ⚠ Skipping record without name")
                continue

            # Normalize name: strip whitespace to match what gets stored in Notion
            candidate_name = str(candidate_name_raw).strip()

            for evaluator in evaluators:
                try:
                    # Check if this evaluator-candidate pair already exists
                    # Using (candidate_name, evaluator_name) as unique key
                    if (candidate_name, evaluator['name']) in existing_pairs:
                        skipped_count += 1
                        continue

                    # Transform record with evaluator
                    properties = self.transform_record(record, evaluator)

                    # Create page in Notion
                    self.notion_client.pages.create(
                        parent={"database_id": self.DATABASE_ID},
                        properties=properties
                    )

                    print(f"  ✓ Created: {candidate_name} × {evaluator['name']}")
                    created_count += 1

                except Exception as e:
                    print(f"  ✗ Failed: {candidate_name} × {evaluator['name']}: {e}")
                    error_count += 1

        print(f"\nSync completed:")
        print(f"  {created_count} evaluator-candidate pairs created")
        print(f"  {skipped_count} pairs already existed (skipped)")
        print(f"  {error_count} errors")

        return created_count, skipped_count, error_count


if __name__ == "__main__":
    sync = RecruitmentSync()
    success = sync.run()
    sys.exit(0 if success else 1)
