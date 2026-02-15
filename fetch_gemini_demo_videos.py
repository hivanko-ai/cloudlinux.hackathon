"""
Fetches the newest email from Gemini with subject starting with
'Notes: "Imunify360 Product Demo"' and lists all attachments (from MIME or from links in the body).
"""
import base64
import email
import os
import re

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.readonly",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_PATH = os.path.join(SCRIPT_DIR, "token.json")


def _get_creds() -> Credentials:
    """Load or refresh credentials (Drive + Gmail)."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return creds


def _is_attachment_part(part: dict) -> bool:
    """True if this part is a real attachment (has filename or attachmentId). Excludes body text/html."""
    if (part.get("filename") or "").strip():
        return True
    if part.get("body", {}).get("attachmentId"):
        return True
    return False


def _collect_attachment_parts(payload: dict, path: str = "payload") -> list[tuple[str, dict]]:
    """
    Recursively walk the MIME part tree and return only parts that are real attachments:
    have a filename or attachmentId (so we skip text/plain, text/html body parts).
    """
    out = []
    parts = payload.get("parts", [])
    if not parts:
        # Leaf: add only if it's an attachment (filename or attachmentId)
        if _is_attachment_part(payload):
            out.append((path, payload))
        return out
    for i, part in enumerate(parts):
        subpath = f"{path}.parts[{i}]"
        out.extend(_collect_attachment_parts(part, subpath))
    return out


def _collect_attachment_parts_from_mime(msg: email.message.Message) -> list[tuple[str, email.message.Message]]:
    """Recursively collect MIME parts that are attachments (have filename or Content-Disposition: attachment)."""
    out = []
    if msg.is_multipart():
        for part in msg.get_payload():
            if not isinstance(part, email.message.Message):
                continue
            out.extend(_collect_attachment_parts_from_mime(part))
    else:
        filename = msg.get_filename()
        disp = (msg.get_content_disposition() or "").lower()
        if filename or disp == "attachment":
            out.append((filename or "(no name)", msg))
    return out


def _decode_part_body_data(body: dict) -> str | None:
    """Decode body.data (base64url) to string, or return None."""
    data = body.get("data")
    if not data:
        return None
    pad = 4 - len(data) % 4
    if pad != 4:
        data = data + "=" * pad
    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return None


def _get_html_body_from_payload(payload: dict) -> str | None:
    """Decode and return the HTML body from the message payload (text/html part)."""
    parts = payload.get("parts", [])
    for part in parts:
        if (part.get("mimeType") or "").strip().lower() == "text/html":
            body = part.get("body", {})
            html = _decode_part_body_data(body)
            if html:
                return html
            # HTML might be in attachment when body is large
            att_id = body.get("attachmentId")
            if att_id:
                return None  # Caller must fetch via _get_html_from_attachment
        nested = _get_html_body_from_payload(part)
        if nested:
            return nested
    return None


def _get_html_from_attachment(service, msg_id: str, payload: dict) -> str | None:
    """Get HTML body, fetching from attachment if the text/html part has attachmentId."""
    html = _get_html_body_from_payload(payload)
    if html:
        return html
    # Find text/html part with attachmentId and fetch it
    parts = payload.get("parts", [])
    for part in parts:
        if (part.get("mimeType") or "").strip().lower() == "text/html":
            att_id = part.get("body", {}).get("attachmentId")
            if att_id:
                try:
                    att = (
                        service.users()
                        .messages()
                        .attachments()
                        .get(userId="me", messageId=msg_id, id=att_id)
                        .execute()
                    )
                    data = att.get("data") or ""
                    pad = 4 - len(data) % 4
                    if pad != 4:
                        data += "=" * pad
                    raw = base64.urlsafe_b64decode(data.encode("ascii"))
                    return raw.decode("utf-8", errors="replace")
                except Exception:
                    pass
            break
        nested = _get_html_from_attachment(service, msg_id, part)
        if nested:
            return nested
    return None


def _extract_drive_urls_from_raw_html(html: str) -> list[tuple[str, str]]:
    """Fallback: find drive.google.com/file/d/ID and docs.google.com/.../d/ID in raw HTML."""
    out = []
    seen = set()
    # drive.google.com/file/d/FILE_ID (with optional /view, ?usp=...)
    for m in re.finditer(
        r"https?://(?:[a-zA-Z0-9.-]+\.)?drive\.google\.com/file/d/([a-zA-Z0-9_.-]+)[^\s\"'<>]*",
        html,
    ):
        file_id = m.group(1)
        url = m.group(0).rstrip("'\">)")
        if file_id not in seen:
            seen.add(file_id)
            out.append((f"Recording {len(out) + 1}", url))
    # docs.google.com/.../d/FILE_ID
    for m in re.finditer(
        r"https?://(?:[a-zA-Z0-9.-]+\.)?docs\.google\.com/[^/]+/d/([a-zA-Z0-9_.-]+)[^\s\"'<>]*",
        html,
    ):
        file_id = m.group(1)
        url = m.group(0).rstrip("'\">)")
        if file_id not in seen:
            seen.add(file_id)
            out.append((f"Recording {len(out) + 1}", url))
    return out


def _extract_linked_attachments_from_html(html: str) -> list[tuple[str, str]]:
    """
    Parse HTML body and extract links that Gmail shows as attachments (e.g. Drive links).
    Uses <a href> first, then regex on raw HTML as fallback.
    Returns list of (display_name, url).
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen_urls = set()
    skip_text = {"here", "link", "click here", "add all to drive", "unsubscribe", "view in browser", "preferences"}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href in seen_urls:
            continue
        name = (a.get_text(strip=True) or "").strip() or href
        if name.lower() in skip_text:
            continue
        is_drive = "drive.google.com" in href or "docs.google.com" in href
        looks_like_attachment = (
            "recording" in name.lower()
            or "recording" in href.lower()
            or "imunify" in name.lower()
            or "demo" in name.lower()
            or "cet" in name.lower()
            or "/2026/" in href
            or "/2024/" in href
        )
        if is_drive or looks_like_attachment:
            seen_urls.add(href)
            if len(name) > 120:
                name = name[:117] + "..."
            out.append((name, href))
    if not out:
        out = _extract_drive_urls_from_raw_html(html)
    return out


def _get_attachment_names_from_raw(service, msg_id: str) -> list[str]:
    """Fetch raw MIME and return list of attachment filenames (for when API returns no attachments)."""
    try:
        raw_msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="raw")
            .execute()
        )
    except HttpError:
        return []
    raw_b64 = raw_msg.get("raw")
    if not raw_b64:
        return []
    pad = 4 - len(raw_b64) % 4
    if pad != 4:
        raw_b64 += "=" * pad
    try:
        raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    except Exception:
        return []
    mime_msg = email.message_from_bytes(raw_bytes)
    parts = _collect_attachment_parts_from_mime(mime_msg)
    return [name for name, _ in parts]


def run() -> tuple[list[str], list[str]]:
    creds = _get_creds()
    service = build("gmail", "v1", credentials=creds)

    # Find newest email from Gemini with subject starting with 'Notes: "Imunify360 Product Demo"'
    # Gmail query: subject contains that text; optionally restrict to sender
    query = 'subject:"Notes: \\"Imunify360 Product Demo\\""'
    try:
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=1)
            .execute()
        )
    except HttpError as e:
        print(f"Gmail API error: {e}")
        return [], []

    messages = results.get("messages", [])
    if not messages:
        print("No matching email found.")
        return [], []

    msg_id = messages[0]["id"]
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
    except HttpError as e:
        print(f"Failed to get message: {e}")
        return [], []

    payload = msg.get("payload", {})
    subject = ""
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == "subject":
            subject = h.get("value", "")
            break
    print(f"Found email: {subject[:80]}...")

    # Collect only real attachments (have filename or attachmentId; skip text/plain, text/html body)
    attachment_parts = _collect_attachment_parts(payload, "payload")
    if not attachment_parts and _is_attachment_part(payload):
        attachment_parts = [("payload", payload)]

    # Build list of attachment names from API
    attachment_names = []
    for path, part in attachment_parts:
        name = (part.get("filename") or "").strip() or "(no filename)"
        attachment_names.append(name)

    # If API found none, try raw MIME then HTML body links (Gemini/Meet often link Drive files in HTML)
    linked_attachments: list[tuple[str, str]] = []
    if not attachment_names:
        attachment_names = _get_attachment_names_from_raw(service, msg_id)
        if not attachment_names:
            html = _get_html_from_attachment(service, msg_id, payload)
            if html:
                linked_attachments = _extract_linked_attachments_from_html(html)
                attachment_names = [name for name, _ in linked_attachments]

    # Build links list aligned with attachment_names (empty string when no URL)
    if linked_attachments:
        attachment_links = [url for _, url in linked_attachments]
    else:
        attachment_links = [""] * len(attachment_names)

    print(f"\n--- All attachments ({len(attachment_names)} total) ---")
    for i, name in enumerate(attachment_names, 1):
        print(f"  {i}. {name}")
        if attachment_links[i - 1]:
            print(f"     {attachment_links[i - 1]}")

    return attachment_names, attachment_links


if __name__ == "__main__":
    run()
