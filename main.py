"""
Creates or reuses the demo folder on Google Drive at:
My Drive/Imunify/Demo/<YYYY>/Imunify360 Product Demo <mm.dd>
Then moves all recordings from the latest Gemini "Notes" email into that folder.
"""
import base64
import os
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote

from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

# Scopes: Drive (create/list/move) and Gmail read
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Global: full path of the demo folder after ensure_demo_folder_on_drive()
DEMO_FOLDER_PATH: str = ""
# Global: Drive file id of the demo folder (for moving files into it)
DEMO_FOLDER_ID: str = ""


def _get_creds() -> Credentials:
    """Load or refresh credentials with Drive + Gmail scopes."""
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


def _get_gmail_service():
    """Build and return authenticated Gmail API service."""
    return build("gmail", "v1", credentials=_get_creds())


def _find_folder_by_name(service, folder_name: str, parent_id: str) -> str | None:
    """Return folder id if a folder with the given name exists under parent_id, else None."""
    try:
        # Escape single quotes in name for the query
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
    folder_name_1 = now.strftime("%Y")  # yyyy
    folder_name_2 = "Imunify360 Product Demo " + now.strftime("%m.%d")  # mm.dd

    service = _get_drive_service()
    root_id = "root"  # My Drive root

    # Imunify
    imunify_id = _get_or_create_folder(service, "Imunify", root_id)
    # Demo
    demo_id = _get_or_create_folder(service, "Demo", imunify_id)
    # YYYY
    year_id = _get_or_create_folder(service, folder_name_1, demo_id)
    # Imunify360 Product Demo mm.dd
    leaf_id = _get_or_create_folder(service, folder_name_2, year_id)

    DEMO_FOLDER_ID = leaf_id
    DEMO_FOLDER_PATH = f"My Drive/Imunify/Demo/{folder_name_1}/{folder_name_2}"
    return DEMO_FOLDER_PATH


# ---- Gmail: latest "Notes: Imunify360 Product Demo" email and recordings ----

# Regex for Google Drive file IDs (multiple URL forms, including after /view and ?usp=)
_DRIVE_FILE_ID_PATTERN = re.compile(
    r"(?:drive\.google\.com/file/d/([a-zA-Z0-9_-]+)"
    r"|drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)"
    r"|/file/d/([a-zA-Z0-9_-]+)(?:/|$|\?|view)"
    r"|id=([a-zA-Z0-9_-]+)(?:&|$))"
)
# URL-encoded form: drive.google.com%2Ffile%2Fd%2FID
_DRIVE_FILE_ID_ENCODED = re.compile(
    r"(?:file%2Fd%2F|%2Ffile%2Fd%2F)([a-zA-Z0-9_-]+)"
)


def _get_latest_notes_email_message(gmail, format: str = "raw"):
    """
    Find the latest email from Gemini with subject containing Notes and Imunify360 Product Demo.
    format: "raw" for body extraction, "full" for attachment parts.
    """
    query = "from:gemini-notes@google.com subject:Notes subject:Imunify360"
    try:
        results = (
            gmail.users()
            .messages()
            .list(userId="me", q=query, maxResults=1)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return None
        msg_id = messages[0]["id"]
        return gmail.users().messages().get(
            userId="me", id=msg_id, format=format
        ).execute()
    except HttpError:
        return None


def _collect_attachment_parts(parts: list, out: list) -> None:
    """Recursively collect parts that have an attachment (body.attachmentId)."""
    for part in parts or []:
        if part.get("body", {}).get("attachmentId"):
            out.append(part)
        _collect_attachment_parts(part.get("parts", []), out)


def _extract_body_from_message(raw_message) -> str:
    """Decode raw Gmail message and return combined text/HTML body as a single string."""
    raw = raw_message.get("raw")
    if not raw:
        return ""
    msg_bytes = base64.urlsafe_b64decode(raw)
    mime = BytesParser(policy=policy.default).parsebytes(msg_bytes)
    parts = []
    for part in mime.walk():
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    parts.append(payload.decode(errors="replace"))
                except Exception:
                    pass
    return "\n".join(parts)


def _extract_drive_file_ids(body: str) -> list[str]:
    """
    Extract unique Google Drive file IDs from email body.
    Checks plain text, URL-decoded text, and all href URLs from HTML.
    """
    seen = set()
    ids = []

    def collect_ids(text: str) -> None:
        if not text:
            return
        for m in _DRIVE_FILE_ID_PATTERN.finditer(text):
            for g in m.groups():
                if g and g not in seen:
                    seen.add(g)
                    ids.append(g)
        # URL-encoded form
        for m in _DRIVE_FILE_ID_ENCODED.finditer(text):
            g = m.group(1)
            if g and g not in seen:
                seen.add(g)
                ids.append(g)

    collect_ids(body)
    # Also search URL-decoded body (links are often stored encoded)
    try:
        decoded = unquote(body)
        if decoded != body:
            collect_ids(decoded)
    except Exception:
        pass
    # Extract hrefs from HTML and search those (handles encoded URLs in links)
    try:
        soup = BeautifulSoup(body, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            collect_ids(href)
            collect_ids(unquote(href))
    except Exception:
        pass
    return ids


def _http_error_message(e: HttpError) -> str:
    """Get a short human-readable message from a Drive/Gmail API HttpError."""
    resp = getattr(e, "resp", None)
    status = getattr(resp, "status", None) or "?"
    try:
        import json
        content = getattr(e, "content", b"")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        data = json.loads(content) if content and content.strip() else {}
        err = data.get("error", {})
        errors = err.get("errors") or [{}]
        reason = (errors[0].get("message") or errors[0].get("reason") or err.get("message") or "").strip()
        if reason:
            return f"HTTP {status}: {reason}"
    except Exception:
        pass
    return f"HTTP {status}: {str(e)!r}"


def _move_file_to_folder(drive_service, file_id: str, folder_id: str) -> tuple[bool, str | None]:
    """Move a Drive file into the given folder. Returns (True, None) on success, (False, error_msg) on failure."""
    try:
        file_meta = drive_service.files().get(fileId=file_id, fields="parents").execute()
        parents = file_meta.get("parents", [])
        drive_service.files().update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=",".join(parents),
        ).execute()
        return True, None
    except HttpError as e:
        return False, _http_error_message(e)


# File names to exclude when moving recordings (case-insensitive)
RECORDING_EXCLUDE_NAMES = ("Notes by Gemini",)


def _get_file_path(drive_service, file_id: str) -> str:
    """Build the Drive path string for a file (e.g. 'My Drive/Folder/Subfolder/filename')."""
    parts = []
    fid = file_id
    try:
        while fid:
            meta = drive_service.files().get(fileId=fid, fields="name,parents").execute()
            name = meta.get("name", "?")
            parts.append(name)
            parents = meta.get("parents", [])
            if not parents:
                break
            fid = parents[0]
            if fid == "root":
                parts.append("My Drive")
                break
        parts.reverse()
        return "/".join(parts) if parts else "?"
    except HttpError:
        return "?"


def _filter_recording_file_ids(drive_service, file_ids: list[str]) -> list[str]:
    """Exclude files that are not recordings (e.g. 'Notes by Gemini'). Returns only IDs to move/copy."""
    if not file_ids:
        return []
    kept = []
    for fid in file_ids:
        try:
            meta = drive_service.files().get(fileId=fid, fields="name").execute()
            name = (meta.get("name") or "").strip()
            if name and name.lower() in (n.lower() for n in RECORDING_EXCLUDE_NAMES):
                continue  # skip this file
            kept.append(fid)
        except HttpError:
            kept.append(fid)  # if we can't get name, include it (could be a recording)
    return kept


def _copy_file_to_folder(drive_service, file_id: str, folder_id: str) -> tuple[str | None, str | None]:
    """Copy a Drive file into the given folder. Returns (new_file_id, None) on success, (None, error_msg) on failure."""
    try:
        meta = drive_service.files().get(fileId=file_id, fields="name,mimeType").execute()
        name = meta.get("name", "Recording")
        body = {"parents": [folder_id], "name": name}
        new_file = drive_service.files().copy(
            fileId=file_id, body=body, fields="id"
        ).execute()
        return new_file.get("id"), None
    except HttpError as e:
        return None, _http_error_message(e)


def _upload_bytes_to_drive_folder(
    drive_service, data: bytes, filename: str, folder_id: str, mime_type: str | None
) -> str | None:
    """Upload bytes as a file into the given Drive folder. Returns new file id or None."""
    from io import BytesIO

    if not mime_type:
        mime_type = "application/octet-stream"
    body = {"name": filename, "parents": [folder_id]}
    try:
        fh = BytesIO(data)
        media = MediaIoBaseUpload(fh, mimetype=mime_type, resumable=False)
        f = drive_service.files().create(
            body=body, media_body=media, fields="id"
        ).execute()
        return f.get("id")
    except HttpError:
        return None


def _download_attachments_and_upload_to_drive(
    gmail, drive_service, msg_id: str, folder_id: str
) -> list[str]:
    """Download all attachments from the message and upload to Drive folder. Returns list of new file ids."""
    msg = gmail.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()
    payload = msg.get("payload", {})
    parts = []
    _collect_attachment_parts(payload.get("parts", []), parts)
    # Single-part message: attachment can be in payload.body
    if not parts and payload.get("body", {}).get("attachmentId"):
        parts.append({"body": payload["body"], "filename": "attachment", "mimeType": payload.get("mimeType")})
    if not parts:
        return []
    uploaded = []
    for part in parts:
        att_id = part["body"].get("attachmentId")
        filename = part.get("filename") or "recording"
        mime = part.get("mimeType") or "application/octet-stream"
        if not att_id:
            continue
        try:
            att = gmail.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            raw = att.get("data")
            if not raw:
                continue
            data = base64.urlsafe_b64decode(raw)
            fid = _upload_bytes_to_drive_folder(
                drive_service, data, filename, folder_id, mime
            )
            if fid:
                uploaded.append(fid)
        except (HttpError, ValueError):
            continue
    return uploaded


def move_recordings_from_gemini_email_to_demo_folder(verbose: bool = True) -> list[str]:
    """
    Get the latest Gemini "Notes: Imunify360 Product Demo" email, then:
    - Extract all Google Drive recording links from the body and move those files
      into the demo folder.
    - Download any email attachments and upload them to the demo folder.

    ensure_demo_folder_on_drive() is called if DEMO_FOLDER_ID is not set.
    Returns list of Drive file IDs that were moved or created (links moved +
    attachments uploaded).
    """
    global DEMO_FOLDER_ID
    if not DEMO_FOLDER_ID:
        ensure_demo_folder_on_drive()

    gmail = _get_gmail_service()
    # Get message id first (from list) so we can use it for attachments
    query = "from:gemini-notes@google.com subject:Notes subject:Imunify360"
    try:
        list_res = (
            gmail.users().messages().list(userId="me", q=query, maxResults=1).execute()
        )
        messages = list_res.get("messages", [])
        if not messages:
            if verbose:
                print("No email found matching: from Gemini, subject Notes + Imunify360")
            return []
        msg_id = messages[0]["id"]
    except HttpError as e:
        if verbose:
            err = e.error_details or e.reason or str(e)
            code = getattr(e, "resp", None)
            status = code.status if code is not None else "?"
            print(f"Gmail search failed (HTTP {status}): {err}")
            if status == 403 or "insufficient" in str(err).lower() or "scope" in str(err).lower():
                print("Fix: Delete token.json and run again to sign in with Gmail access.")
                print("     Also ensure Gmail API is enabled at: https://console.cloud.google.com/apis/library/gmail.googleapis.com")
        return []

    raw_msg = gmail.users().messages().get(
        userId="me", id=msg_id, format="raw"
    ).execute()
    body = _extract_body_from_message(raw_msg)
    all_file_ids = _extract_drive_file_ids(body)
    drive = _get_drive_service()
    file_ids = _filter_recording_file_ids(drive, all_file_ids)  # exclude e.g. "Notes by Gemini"

    if verbose:
        print(f"Found email (id={msg_id}), body length={len(body)}, Drive links={len(all_file_ids)}, recordings to add={len(file_ids)}")

    # First: print the path of each recording file
    if verbose and file_ids:
        print("Recording file paths (current location):")
        for i, fid in enumerate(file_ids, 1):
            path = _get_file_path(drive, fid)
            print(f"  {i}. {path}")
        print(f"Target folder: {DEMO_FOLDER_PATH}")
        print("Copying recordings to target folder...")

    added = []  # file ids now in demo folder (moved or copied)
    first_error = None  # so we can show the user why it failed
    for fid in file_ids:
        ok, err = _move_file_to_folder(drive, fid, DEMO_FOLDER_ID)
        if ok:
            added.append(fid)
            continue
        new_id, copy_err = _copy_file_to_folder(drive, fid, DEMO_FOLDER_ID)
        if new_id:
            added.append(new_id)
        elif first_error is None:
            first_error = copy_err or err

    # If nothing was added (likely missing permission), open browser to re-authorize and retry once
    if file_ids and not added:
        token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
        if os.path.exists(token_path):
            if verbose:
                print("Opening browser to request full Drive access. Please sign in and grant access to your files.")
            try:
                os.remove(token_path)
            except OSError:
                pass
            _get_creds()
            drive = _get_drive_service()
            for fid in file_ids:
                ok, _ = _move_file_to_folder(drive, fid, DEMO_FOLDER_ID)
                if ok:
                    added.append(fid)
                else:
                    new_id, _ = _copy_file_to_folder(drive, fid, DEMO_FOLDER_ID)
                    if new_id:
                        added.append(new_id)
            if verbose and added:
                print(f"After re-authorization: added {len(added)} of {len(file_ids)} recording(s).")

    if verbose and file_ids:
        print(f"Added {len(added)} of {len(file_ids)} recording(s) to demo folder (moved or copied).")
    if verbose and file_ids and not added:
        if first_error:
            print(f"Drive API error (first file): {first_error}")
        print("Possible causes:")
        print("  • Files are shared from another account (e.g. Gemini) with view-only or no copy permission.")
        print("  • Links point to a folder or shortcut, not a file (copy works only for files).")
        print("  • App is in Testing mode: add your Google account as a test user in Cloud Console.")
        print("  • Target folder is on a Shared Drive: the app uses 'My Drive'; try a folder in My Drive.")
        print("  • Run the script again and complete the browser sign-in to grant full Drive access.")

    # Also upload any attachments (recordings may be attached files)
    uploaded = _download_attachments_and_upload_to_drive(
        gmail, drive, msg_id, DEMO_FOLDER_ID
    )
    if verbose and uploaded:
        print(f"Uploaded {len(uploaded)} attachment(s) to demo folder.")

    return added + uploaded


if __name__ == "__main__":
    ensure_demo_folder_on_drive()
    print("Demo folder path:", DEMO_FOLDER_PATH)
    print("Demo folder id:  ", DEMO_FOLDER_ID)
    moved = move_recordings_from_gemini_email_to_demo_folder()
    print("Moved recordings (file ids):", moved)
