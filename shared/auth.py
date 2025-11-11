"""
Shared authentication for Google and Notion APIs
Credentials loaded from environment variables
"""

import os
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread
from notion_client import Client

# Load environment variables once
load_dotenv()


class GoogleAuth:
    """Universal Google Sheets authentication"""

    _client = None  # Singleton instance

    @classmethod
    def get_client(cls):
        """Get authenticated Google Sheets client"""
        if cls._client is None:
            # Get credentials from environment
            client_email = os.getenv('GOOGLE_CLIENT_EMAIL')
            private_key = os.getenv('GOOGLE_PRIVATE_KEY')

            if not client_email or not private_key:
                raise ValueError(
                    "Missing Google credentials. Set GOOGLE_CLIENT_EMAIL and "
                    "GOOGLE_PRIVATE_KEY in .env file"
                )

            # Handle escaped newlines in private key
            private_key = private_key.replace('\\n', '\n')

            # Create credentials from environment
            creds_info = {
                "type": "service_account",
                "project_id": "theatre-sgh",
                "private_key_id": "29ef31c6d76f0e79565639ea4e79fcce50807ff2",
                "private_key": private_key,
                "client_email": client_email,
                "client_id": "118265202546856213237",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{client_email.replace('@', '%40')}",
                "universe_domain": "googleapis.com"
            }

            scopes = [
                'https://www.googleapis.com/auth/spreadsheets.readonly',
                'https://www.googleapis.com/auth/drive.readonly'
            ]

            creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
            cls._client = gspread.authorize(creds)

            print("✓ Google Sheets authentication successful")

        return cls._client


class NotionAuth:
    """Universal Notion authentication"""

    _client = None  # Singleton instance

    @classmethod
    def get_client(cls):
        """Get authenticated Notion client"""
        if cls._client is None:
            token = os.getenv('NOTION_API_TOKEN')

            if not token:
                raise ValueError(
                    "Missing Notion token. Set NOTION_API_TOKEN in .env file"
                )

            cls._client = Client(auth=token)
            print("✓ Notion authentication successful")

        return cls._client
