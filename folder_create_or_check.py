"""
Creates or reuses the demo folder on Google Drive at:
My Drive/Imunify/Demo/<YYYY>/Imunify360 Product Demo <mm.dd>
"""
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]

DEMO_FOLDER_PATH: str = ""
DEMO_FOLDER_ID: str = ""


def _get_creds() -> Credentials:
    """Load or refresh credentials for Drive."""
    token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
    creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
    return creds


def _get_drive_service():
    """Build and return authenticated Google Drive API v3 service."""
    return build("drive", "v3", credentials=_get_creds())


def get_drive_service():
    """Return authenticated Google Drive API v3 service (for use by other modules)."""
    return _get_drive_service()


def _find_folder_by_name(service, folder_name: str, parent_id: str) -> str | None:
    """Return folder id if a folder with the given name exists under parent_id, else None."""
    try:
        safe_name = folder_name.replace("\\", "\\\\").replace("'", "\\'")
        q = (
            f"mimeType='application/vnd.google-apps.folder' "
            f"and name='{safe_name}' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        response = (
            service.files()
            .list(q=q, spaces="drive", fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = response.get("files", [])
        return files[0]["id"] if files else None
    except HttpError:
        return None


def _create_folder(service, folder_name: str, parent_id: str) -> str:
    """Create a folder with the given name under parent_id and return its id."""
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=body, fields="id").execute()
    return folder["id"]


def _get_or_create_folder(service, folder_name: str, parent_id: str) -> str:
    """Get existing folder id or create the folder; return its id."""
    folder_id = _find_folder_by_name(service, folder_name, parent_id)
    if folder_id is not None:
        return folder_id
    return _create_folder(service, folder_name, parent_id)


def ensure_demo_folder_on_drive() -> str:
    """
    Ensure the demo folder exists on Google Drive at:
    My Drive/Imunify/Demo/<YYYY>/Imunify360 Product Demo <mm.dd>

    Uses system date for YYYY and mm.dd. If any part of the path already exists,
    reuses it. Sets DEMO_FOLDER_PATH and DEMO_FOLDER_ID; returns the path.
    """
    global DEMO_FOLDER_PATH, DEMO_FOLDER_ID

    now = datetime.now()
    folder_name_1 = now.strftime("%Y")
    folder_name_2 = "Imunify360 Product Demo " + now.strftime("%m.%d")

    service = _get_drive_service()
    root_id = "root"

    imunify_id = _get_or_create_folder(service, "Imunify", root_id)
    demo_id = _get_or_create_folder(service, "Demo", imunify_id)
    year_id = _get_or_create_folder(service, folder_name_1, demo_id)
    leaf_id = _get_or_create_folder(service, folder_name_2, year_id)

    DEMO_FOLDER_ID = leaf_id
    DEMO_FOLDER_PATH = f"My Drive/Imunify/Demo/{folder_name_1}/{folder_name_2}"
    return DEMO_FOLDER_PATH
