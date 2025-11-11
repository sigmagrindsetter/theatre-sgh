"""
Base sync service for Google Sheets to Notion
All integrations inherit from this class
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from .auth import GoogleAuth, NotionAuth


class BaseSyncService(ABC):
    """
    Base class for syncing Google Sheets to Notion

    Usage:
        class MySync(BaseSyncService):
            SHEET_ID = "your-sheet-id"
            DATABASE_ID = "your-database-id"
            UNIQUE_KEY = "Email"  # Notion column name to check for duplicates

            def get_unique_value_from_record(self, record):
                # Return the value to use for duplicate checking
                return record["Email Column in Sheet"]

            def transform_record(self, record):
                # Custom transformation logic
                return properties
    """

    # Subclasses must define these
    SHEET_ID: str = None
    DATABASE_ID: str = None
    SHEET_NAME: str = None  # Optional, defaults to first sheet
    UNIQUE_KEY: str = None  # Notion column name to use for duplicate detection

    def __init__(self):
        if not self.SHEET_ID or not self.DATABASE_ID:
            raise ValueError(
                f"{self.__class__.__name__} must define SHEET_ID and DATABASE_ID"
            )

        # Get authenticated clients (shared across all instances)
        self.google_client = GoogleAuth.get_client()
        self.notion_client = NotionAuth.get_client()

    def get_sheet_data(self) -> List[Dict[str, Any]]:
        """Fetch all records from Google Sheet"""
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
        """
        Get existing records from Notion to check for duplicates
        Returns dict of {unique_key_value: page_id}
        """
        if not self.UNIQUE_KEY:
            return {}

        print(f"Checking existing records in Notion (by {self.UNIQUE_KEY})...")

        try:
            existing = {}
            results = self.notion_client.databases.query(database_id=self.DATABASE_ID)

            for page in results.get('results', []):
                props = page['properties']
                key_prop = props.get(self.UNIQUE_KEY, {})

                # Extract value based on property type
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
        """
        Extract unique identifier value from a Google Sheets record

        Subclasses can override this method to specify which column to use.
        Default behavior: looks for UNIQUE_KEY in the record.

        Args:
            record: Dictionary from Google Sheets

        Returns:
            The value to use for duplicate checking, or None
        """
        return record.get(self.UNIQUE_KEY)

    @abstractmethod
    def transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform a Google Sheet record into Notion properties

        Args:
            record: Dictionary with column names as keys

        Returns:
            Dictionary of Notion properties

        Example:
            def transform_record(self, record):
                return {
                    "Name": {"title": [{"text": {"content": record["Name"]}}]},
                    "Email": {"email": record["Email"]},
                    "Status": {"select": {"name": record["Status"]}}
                }
        """
        pass

    def sync_to_notion(self, records: List[Dict[str, Any]]) -> tuple[int, int, int]:
        """
        Sync records to Notion database
        Returns (created_count, skipped_count, error_count)
        """
        print(f"Syncing {len(records)} records to Notion (ID: {self.DATABASE_ID})...")

        # Get existing records to avoid duplicates
        existing_records = self.get_existing_records()

        created_count = 0
        skipped_count = 0
        error_count = 0

        for record in records:
            try:
                # Check for duplicate if UNIQUE_KEY is defined
                if self.UNIQUE_KEY:
                    unique_value = self.get_unique_value_from_record(record)
                    if unique_value and unique_value in existing_records:
                        print(f"  ⊘ Skipped (exists): {unique_value}")
                        skipped_count += 1
                        continue

                # Transform record using subclass implementation
                properties = self.transform_record(record)

                # Create page in Notion
                self.notion_client.pages.create(
                    parent={"database_id": self.DATABASE_ID},
                    properties=properties
                )

                # Show identifier
                identifier = self.get_unique_value_from_record(record) or next((v for v in record.values() if v), "Unknown")
                print(f"  ✓ Created: {identifier}")
                created_count += 1

            except Exception as e:
                print(f"  ✗ Failed to sync record: {e}")
                error_count += 1

        print(f"\nSync completed: {created_count} created, {skipped_count} skipped, {error_count} errors")
        return created_count, skipped_count, error_count

    def run(self) -> bool:
        """Main sync execution"""
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
