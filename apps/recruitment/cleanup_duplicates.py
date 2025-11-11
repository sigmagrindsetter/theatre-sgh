#!/usr/bin/env python3
"""
Remove duplicate evaluator-candidate pairs from Notion
Keeps records with Ocena first, then Komentarz, then oldest record
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth
from config import DATABASE_ID


def cleanup_duplicates(force=False):
    """Find and remove duplicate evaluator-candidate pairs"""
    notion = NotionAuth.get_client()

    print(f"\n{'=' * 60}")
    print("Cleaning up duplicate evaluator-candidate pairs")
    print(f"{'=' * 60}\n")

    # Fetch ALL records with pagination
    print("Fetching all records from Notion...")
    all_records = []
    has_more = True
    next_cursor = None

    while has_more:
        query_params = {"database_id": DATABASE_ID}
        if next_cursor:
            query_params["start_cursor"] = next_cursor

        response = notion.databases.query(**query_params)
        all_records.extend(response.get('results', []))
        has_more = response.get('has_more', False)
        next_cursor = response.get('next_cursor')

        print(f"  Fetched {len(all_records)} records so far...")

    print(f"✓ Total records fetched: {len(all_records)}\n")

    # Group records by (candidate_name, evaluator_name)
    groups = defaultdict(list)

    for record in all_records:
        props = record['properties']

        # Get candidate name
        name_prop = props.get('Imię i nazwisko', {})
        candidate_name = None
        if name_prop.get('title') and name_prop['title']:
            candidate_name = name_prop['title'][0]['text']['content']

        # Get evaluator
        evaluator_prop = props.get('Oceniający', {})
        evaluator = None
        if evaluator_prop.get('people') and evaluator_prop['people']:
            evaluator = evaluator_prop['people'][0].get('name')

        # Get Ocena
        ocena_prop = props.get('Ocena', {})
        ocena = ocena_prop.get('number')

        # Get Komentarz
        komentarz_prop = props.get('Komentarz', {})
        komentarz = None
        if komentarz_prop.get('rich_text') and komentarz_prop['rich_text']:
            komentarz = komentarz_prop['rich_text'][0]['text']['content']

        # Get creation time
        created_time = record.get('created_time')

        if candidate_name and evaluator:
            key = (candidate_name, evaluator)
            groups[key].append({
                'id': record['id'],
                'ocena': ocena,
                'komentarz': komentarz,
                'created_time': created_time,
                'candidate': candidate_name,
                'evaluator': evaluator
            })

    print(f"Found {len(groups)} unique candidate-evaluator pairs")

    # Find duplicates
    duplicates_to_delete = []

    for key, records in groups.items():
        if len(records) > 1:
            candidate, evaluator = key
            print(f"\nDuplicate: {candidate} × {evaluator} ({len(records)} records)")

            # Sort by priority:
            # 1. Has Ocena (number exists)
            # 2. Has Komentarz (text exists)
            # 3. Oldest (first created)
            def sort_priority(r):
                has_ocena = r['ocena'] is not None
                has_komentarz = bool(r['komentarz'])
                created = r['created_time']
                # Return tuple: (has_ocena desc, has_komentarz desc, created asc)
                return (not has_ocena, not has_komentarz, created)

            sorted_records = sorted(records, key=sort_priority)

            # Keep first (highest priority), delete rest
            keep = sorted_records[0]
            delete = sorted_records[1:]

            ocena_str = f"Ocena: {keep['ocena']}" if keep['ocena'] else "No Ocena"
            kom_str = f"Komentarz: Yes" if keep['komentarz'] else "No Komentarz"
            print(f"  ✓ Keeping: {keep['id'][:8]}... ({ocena_str}, {kom_str})")

            for rec in delete:
                duplicates_to_delete.append(rec)
                print(f"  ✗ Deleting: {rec['id'][:8]}...")

    print(f"\n{'=' * 60}")
    print(f"Summary: {len(duplicates_to_delete)} duplicates to delete")
    print(f"{'=' * 60}\n")

    if not duplicates_to_delete:
        print("No duplicates found. Database is clean!")
        return

    # Ask for confirmation (skip if --force flag is used)
    if not force:
        response = input(f"Delete {len(duplicates_to_delete)} duplicate records? (yes/no): ")

        if response.lower() != 'yes':
            print("Cancelled. No records deleted.")
            return
    else:
        print(f"Force mode enabled. Proceeding to delete {len(duplicates_to_delete)} duplicate records...")

    # Delete duplicates
    print("\nDeleting duplicates...")
    deleted_count = 0

    for rec in duplicates_to_delete:
        try:
            notion.pages.update(
                page_id=rec['id'],
                archived=True
            )
            print(f"  ✓ Deleted: {rec['candidate']} × {rec['evaluator']}")
            deleted_count += 1
        except Exception as e:
            print(f"  ✗ Failed to delete {rec['id']}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Cleanup completed: {deleted_count} duplicates removed")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Remove duplicate evaluator-candidate pairs from Notion"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt and delete duplicates automatically"
    )
    args = parser.parse_args()

    cleanup_duplicates(force=args.force)
