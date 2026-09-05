#!/usr/bin/env python3
import sys
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared import BaseSyncService
from config import SHEET_ID, DATABASE_ID, SHEET_NAME, EVALUATORS_DATABASE_ID


class RecruitmentSync(BaseSyncService):
    SHEET_ID = SHEET_ID
    DATABASE_ID = DATABASE_ID
    SHEET_NAME = SHEET_NAME

    UNIQUE_KEY = "Email"
    UNIQUE_KEY_SHEET = "Adres e-mail:"

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
        print("\nFetching active evaluators from database...")

        try:
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

                if person_prop.get('people') and person_prop['people']:
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
        print("Checking existing evaluator-candidate pairs in Notion...")

        try:
            existing = {}
            has_more = True
            next_cursor = None

            while has_more:
                query_params = {"database_id": self.DATABASE_ID}
                if next_cursor:
                    query_params["start_cursor"] = next_cursor

                response = self.notion_client.databases.query(**query_params)

                for page in response.get('results', []):
                    props = page['properties']

                    name_prop = props.get('Imię i nazwisko', {})
                    candidate_name = None
                    if name_prop.get('title') and name_prop['title']:
                        candidate_name = name_prop['title'][0]['text']['content']

                    evaluator_prop = props.get('Oceniający', {})
                    evaluator = None
                    if evaluator_prop.get('people') and evaluator_prop['people']:
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
        try:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"]:
                try:
                    dt = datetime.strptime(timestamp_str.strip(), fmt)
                    return dt.isoformat()
                except ValueError:
                    continue

            return None
        except Exception:
            return None

    def transform_record(self, record, evaluator=None):
        properties = {}

        for sheet_col, notion_col in self.COLUMN_MAPPING.items():
            if sheet_col not in record:
                found = False
                for key in record.keys():
                    if key.strip() == sheet_col.strip():
                        sheet_col = key
                        found = True
                        break

                if not found:
                    continue

            value = record[sheet_col]

            if not value or (isinstance(value, str) and not value.strip()):
                continue

            value = str(value).strip()

            if notion_col == "Imię i nazwisko":
                properties[notion_col] = {
                    "title": [{"text": {"content": value}}]
                }
            elif notion_col == "Czas":
                date_iso = self.parse_timestamp(value)
                if date_iso:
                    properties[notion_col] = {
                        "date": {"start": date_iso}
                    }
                else:
                    properties[notion_col] = {
                        "date": {"start": value[:10]}
                    }
            elif notion_col == "Email":
                properties[notion_col] = {
                    "email": value
                }
            elif notion_col == "Filmik":
                if value.startswith("http"):
                    properties[notion_col] = {"url": value}
            else:
                properties[notion_col] = {
                    "rich_text": [{"text": {"content": value}}]
                }

        if evaluator:
            properties["Oceniający"] = {
                "people": [{"id": evaluator['id']}]
            }

        return properties

    def sync_to_notion(self, records):
        print(f"\nSyncing {len(records)} candidates to Notion...")

        evaluators = self.get_active_evaluators()

        if not evaluators:
            print("⚠ No active evaluators found. Skipping sync.")
            return 0, 0, 0

        existing_pairs = self.get_existing_records()

        created_count = 0
        skipped_count = 0
        error_count = 0

        for record in records:
            candidate_name_raw = record.get("Imię i nazwisko:", "Unknown")
            if not candidate_name_raw or candidate_name_raw == "Unknown":
                print("  ⚠ Skipping record without name")
                continue

            candidate_name = str(candidate_name_raw).strip()

            for evaluator in evaluators:
                try:
                    if (candidate_name, evaluator['name']) in existing_pairs:
                        skipped_count += 1
                        continue

                    properties = self.transform_record(record, evaluator)

                    self.notion_client.pages.create(
                        parent={"database_id": self.DATABASE_ID},
                        properties=properties
                    )

                    print(f"  ✓ Created: {candidate_name} × {evaluator['name']}")
                    created_count += 1

                except Exception as e:
                    print(f"  ✗ Failed: {candidate_name} × {evaluator['name']}: {e}")
                    error_count += 1

        print("\nSync completed:")
        print(f"  {created_count} evaluator-candidate pairs created")
        print(f"  {skipped_count} pairs already existed (skipped)")
        print(f"  {error_count} errors")

        return created_count, skipped_count, error_count


if __name__ == "__main__":
    sync = RecruitmentSync()
    success = sync.run()
    sys.exit(0 if success else 1)
