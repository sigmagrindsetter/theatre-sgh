from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from .auth import GoogleAuth, NotionAuth


class BaseSyncService(ABC):
    SHEET_ID: str = None
    DATABASE_ID: str = None
    SHEET_NAME: str = None
    UNIQUE_KEY: str = None

    def __init__(self):
        if not self.SHEET_ID or not self.DATABASE_ID:
            raise ValueError(
                f"{self.__class__.__name__} must define SHEET_ID and DATABASE_ID"
            )

        self.google_client = GoogleAuth.get_client()
        self.notion_client = NotionAuth.get_client()

    def get_sheet_data(self) -> List[Dict[str, Any]]:
        print(f"Fetching data from Google Sheets (ID: {self.SHEET_ID})...")

        try:
            spreadsheet = self.google_client.open_by_key(self.SHEET_ID)

            if self.SHEET_NAME:
                sheet = spreadsheet.worksheet(self.SHEET_NAME)
            else:
                sheet = spreadsheet.sheet1

            records = sheet.get_all_records()
            print(f"✓ Found {len(records)} records")
            return records

        except Exception as e:
            print(f"✗ Error fetching sheet data: {e}")
            return []

    def get_existing_records(self) -> Dict[str, str]:
        if not self.UNIQUE_KEY:
            return {}

        print(f"Checking existing records in Notion (by {self.UNIQUE_KEY})...")

        try:
            existing = {}
            results = self.notion_client.databases.query(database_id=self.DATABASE_ID)

            for page in results.get('results', []):
                props = page['properties']
                key_prop = props.get(self.UNIQUE_KEY, {})

                key_value = None
                if key_prop.get('email'):
                    key_value = key_prop['email']
                elif key_prop.get('title'):
                    if key_prop['title']:
                        key_value = key_prop['title'][0]['text']['content']
                elif key_prop.get('rich_text'):
                    if key_prop['rich_text']:
                        key_value = key_prop['rich_text'][0]['text']['content']

                if key_value:
                    existing[key_value] = page['id']

            print(f"✓ Found {len(existing)} existing records in Notion")
            return existing

        except Exception as e:
            print(f"⚠ Could not fetch existing records: {e}")
            return {}

    def get_unique_value_from_record(self, record: Dict[str, Any]) -> Optional[str]:
        return record.get(self.UNIQUE_KEY)

    @abstractmethod
    def transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def sync_to_notion(self, records: List[Dict[str, Any]]) -> tuple[int, int, int]:
        print(f"Syncing {len(records)} records to Notion (ID: {self.DATABASE_ID})...")

        existing_records = self.get_existing_records()

        created_count = 0
        skipped_count = 0
        error_count = 0

        for record in records:
            try:
                if self.UNIQUE_KEY:
                    unique_value = self.get_unique_value_from_record(record)
                    if unique_value and unique_value in existing_records:
                        print(f"  ⊘ Skipped (exists): {unique_value}")
                        skipped_count += 1
                        continue

                properties = self.transform_record(record)

                self.notion_client.pages.create(
                    parent={"database_id": self.DATABASE_ID},
                    properties=properties
                )

                identifier = self.get_unique_value_from_record(record) or next((v for v in record.values() if v), "Unknown")
                print(f"  ✓ Created: {identifier}")
                created_count += 1

            except Exception as e:
                print(f"  ✗ Failed to sync record: {e}")
                error_count += 1

        print(f"\nSync completed: {created_count} created, {skipped_count} skipped, {error_count} errors")
        return created_count, skipped_count, error_count

    def run(self) -> bool:
        print(f"\n{'=' * 60}")
        print(f"{self.__class__.__name__} - Sync Started")
        print(f"{'=' * 60}\n")

        try:
            records = self.get_sheet_data()

            if records:
                self.sync_to_notion(records)
            else:
                print("No records to sync")

            print(f"\n{'=' * 60}")
            print("Sync Completed Successfully")
            print(f"{'=' * 60}\n")
            return True

        except Exception as e:
            print(f"\n✗ Sync failed: {e}")
            return False
