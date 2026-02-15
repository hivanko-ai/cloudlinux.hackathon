"""
Finds attachments whose name contains the word "recording",
copies those (Drive-linked) files to the demo folder from folder_create_or_check,
and/or downloads them to a local folder.
"""
import io
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

from googleapiclient.http import MediaIoBaseUpload

import folder_create_or_check as fcc
from fetch_gemini_demo_videos import run

import get_names_from_slite as gns

# Local folder where recording files are saved
RECORDINGS_LOCAL_FOLDER = r"C:\Users\elena\OneDrive\Документы\Test_for_Demo"


def _name_contains_recording(name: str) -> bool:
    """True if name contains the word 'recording' (case-insensitive)."""
    return "recording" in (name or "").lower()


def _safe_local_filename(name: str) -> str:
    """Make a safe filename for Windows (no path, no invalid chars)."""
    name = os.path.basename((name or "recording").strip())
    for c in '<>:"/\\|?*':
        name = name.replace(c, "_")
    return name or "recording"


def _get_recording_entries() -> list[tuple[str, str]]:
    """Get (name, url) list for recordings (or all linked attachments if none match)."""
    names, links = run()
    all_linked = [(name, link) for name, link in zip(names, links) if link]
    recording_entries = [
        (name, link) for name, link in all_linked if _name_contains_recording(name)
    ]
    if not recording_entries:
        recording_entries = all_linked
    return recording_entries


def _extract_drive_file_id(url: str) -> str | None:
    """Extract Google Drive file ID from a Drive URL, or return None."""
    if not url:
        return None
    # Unwrap Google redirect (e.g. https://www.google.com/url?q=https%3A%2F%2Fdrive.google.com%2F...)
    parsed = urlparse(url)
    if "google.com" in parsed.netloc and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        for key in ("q", "url"):
            if key in qs:
                url = unquote(qs[key][0])
                break
    # Drive file ID: alphanumeric, dash, underscore, dot
    id_pat = r"[a-zA-Z0-9_.-]+"
    # /file/d/FILE_ID or /open?id=FILE_ID or /uc?id=FILE_ID
    m = re.search(r"/file/d/(" + id_pat + r")", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(" + id_pat + r")", url)
    if m:
        return m.group(1)
    # docs.google.com or drive.google.com .../d/FILE_ID
    m = re.search(r"/d/(" + id_pat + r")(?:/|$|\?)", url)
    if m:
        return m.group(1)
    return None


def copy_recording_attachments_to_drive() -> list[str]:
    """
    Get attachments from the Gemini demo email, filter to names that contain "recording"
    (or use all linked attachments if none match), and copy each via its Drive link
    to the demo folder. Returns list of copied file names.
    """
    recording_entries = _get_recording_entries()
    if not recording_entries:
        return []

    print(f"Using {len(recording_entries)} attachment link(s) to copy to Drive.")
    fcc.ensure_demo_folder_on_drive()
    if not fcc.DEMO_FOLDER_ID:
        return []

    service = fcc.get_drive_service()
    copied = []
    for name, url in recording_entries:
        file_id = _extract_drive_file_id(url)
        if not file_id:
            print(f"No file ID from URL (first 80 chars): {url[:80]!r}...")
            continue
        try:
            result = (
                service.files()
                .copy(
                    fileId=file_id,
                    body={"parents": [fcc.DEMO_FOLDER_ID], "name": name},
                    supportsAllDrives=True,
                )
                .execute()
            )
            copied.append(result.get("name", name))
        except Exception as copy_err:
            # Fallback: download file and upload to our folder (works for shared "view" links)
            try:
                meta = (
                    service.files()
                    .get(
                        fileId=file_id,
                        supportsAllDrives=True,
                        fields="name,mimeType",
                    )
                    .execute()
                )
                mime = meta.get("mimeType") or "application/octet-stream"
                request = service.files().get_media(
                    fileId=file_id,
                    supportsAllDrives=True,
                )
                data = request.execute()
                media = MediaIoBaseUpload(
                    io.BytesIO(data),
                    mimetype=mime,
                    resumable=False,
                )
                result = (
                    service.files()
                    .create(
                        body={
                            "name": name,
                            "parents": [fcc.DEMO_FOLDER_ID],
                        },
                        media_body=media,
                        fields="name",
                    )
                    .execute()
                )
                copied.append(result.get("name", name))
            except Exception as e:
                print(f"Could not copy {name!r}: {copy_err}; fallback failed: {e}")
    return copied


def download_recordings_to_folder(local_path: str | None = None) -> list[str]:
    """
    Download all recording files (from the Gemini email links) to a local folder.
    Uses RECORDINGS_LOCAL_FOLDER if local_path is None. Returns list of saved file paths.
    """
    folder = (local_path or RECORDINGS_LOCAL_FOLDER).rstrip(os.sep)
    os.makedirs(folder, exist_ok=True)

    recording_entries = _get_recording_entries()
    if not recording_entries:
        return []

    print(f"Downloading {len(recording_entries)} recording(s) to {folder}.")
    service = fcc.get_drive_service()
    saved = []
    for name, url in recording_entries:
        file_id = _extract_drive_file_id(url)
        if not file_id:
            print(f"No file ID from URL: {url[:80]!r}...")
            continue
        try:
            meta = (
                service.files()
                .get(
                    fileId=file_id,
                    supportsAllDrives=True,
                    fields="name,mimeType",
                )
                .execute()
            )
            data = (
                service.files()
                .get_media(fileId=file_id, supportsAllDrives=True)
                .execute()
            )
            filename = _safe_local_filename(name)
            if not filename.strip():
                filename = meta.get("name") or "recording"
                filename = _safe_local_filename(filename)
            if not os.path.splitext(filename)[1]:
                mime = (meta.get("mimeType") or "").lower()
                if "video" in mime or "mp4" in mime:
                    filename += ".mp4"
                else:
                    filename += ".bin"
            out_path = os.path.join(folder, filename)
            n = 1
            while os.path.exists(out_path):
                base, ext = os.path.splitext(filename)
                out_path = os.path.join(folder, f"{base}_{n}{ext}")
                n += 1
            with open(out_path, "wb") as f:
                f.write(data)
            saved.append(out_path)
        except Exception as e:
            print(f"Could not download {name!r}: {e}")
    return saved


def rename_local_recordings_with_feature_names(local_folder: str | None = None) -> list[str]:
    """
    Get feature names from Slite (get_names_from_slite), then rename each file in the
    local recordings folder to the corresponding feature name. Files are ordered by
    mtime (oldest first) to match Slite's "last n" order. Returns list of new file names.
    """
    folder = (local_folder or RECORDINGS_LOCAL_FOLDER).rstrip(os.sep)
    if not os.path.isdir(folder):
        return []

    feature_names = gns.get_feature_names_from_slite(recordings_folder=folder)
    if not feature_names:
        print("No feature names from Slite — skipping rename.")
        return []

    paths = [
        os.path.join(folder, n)
        for n in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, n))
    ]
    if not paths:
        return []

    # Sort by mtime (oldest first) so order matches "first downloaded" = first of last n in Slite
    paths.sort(key=lambda p: os.path.getmtime(p))
    n = min(len(paths), len(feature_names))
    if n == 0:
        return []

    # Two-pass rename to avoid overwriting: first -> __temp_i.ext, then __temp_i -> final name
    renames = []
    for i in range(n):
        old_path = paths[i]
        ext = os.path.splitext(old_path)[1] or ""
        base = _safe_local_filename(feature_names[i]).strip()
        if not base:
            base = os.path.splitext(os.path.basename(old_path))[0]
        new_name = base + ext
        temp_path = os.path.join(folder, f"__temp_{i}{ext}")
        renames.append((old_path, temp_path, new_name))

    for old_path, temp_path, _ in renames:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            os.rename(old_path, temp_path)
        except Exception as e:
            print(f"Rename to temp failed for {old_path!r}: {e}")
            return []

    renamed = []
    for _, temp_path, new_name in renames:
        final_path = os.path.join(folder, new_name)
        try:
            if os.path.exists(final_path) and os.path.abspath(final_path) != os.path.abspath(temp_path):
                base, ext = os.path.splitext(new_name)
                num = 1
                while os.path.exists(final_path):
                    final_path = os.path.join(folder, f"{base}_{num}{ext}")
                    num += 1
                new_name = os.path.basename(final_path)
            os.rename(temp_path, final_path)
            renamed.append(new_name)
        except Exception as e:
            print(f"Rename to final failed for {temp_path!r}: {e}")
            try:
                os.rename(temp_path, os.path.join(folder, os.path.basename(temp_path)))
            except Exception:
                pass
    if renamed:
        print(f"Renamed {len(renamed)} recording(s) with feature names from Slite.")
    return renamed


def _guess_mime_type(filename: str) -> str:
    """Return a MIME type based on file extension."""
    ext = (os.path.splitext(filename)[1] or "").lower()
    mime = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".m4v": "video/x-m4v",
        ".wmv": "video/x-ms-wmv",
    }
    return mime.get(ext, "application/octet-stream")


def upload_local_recordings_to_drive(local_folder: str | None = None) -> list[str]:
    """
    Upload all files from the local recordings folder to the Google Drive demo folder
    (from folder_create_or_check). Returns list of uploaded file names on Drive.
    Before uploading, fetches feature names from Slite and renames each recording accordingly.
    """
    folder = (local_folder or RECORDINGS_LOCAL_FOLDER).rstrip(os.sep)
    if not os.path.isdir(folder):
        print(f"Folder does not exist: {folder}")
        return []

    # Rename recordings with feature names from Slite before uploading
    rename_local_recordings_with_feature_names(folder)

    fcc.ensure_demo_folder_on_drive()
    if not fcc.DEMO_FOLDER_ID:
        print("Demo folder on Drive could not be created or found.")
        return []

    service = fcc.get_drive_service()
    file_names = [n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))]
    if not file_names:
        print(f"No files found in {folder}")
        return []

    print(f"Uploading {len(file_names)} file(s) to Drive: {fcc.DEMO_FOLDER_PATH or 'demo folder'}.")
    uploaded = []
    for name in file_names:
        path = os.path.join(folder, name)
        try:
            mime = _guess_mime_type(name)
            with open(path, "rb") as f:
                media = MediaIoBaseUpload(
                    f,
                    mimetype=mime,
                    resumable=True,
                )
                result = (
                    service.files()
                    .create(
                        body={
                            "name": name,
                            "parents": [fcc.DEMO_FOLDER_ID],
                        },
                        media_body=media,
                        fields="name,id",
                    )
                    .execute()
                )
            uploaded.append(result.get("name", name))
        except Exception as e:
            print(f"Could not upload {name!r}: {e}")
    return uploaded


if __name__ == "__main__":
    local_folder = RECORDINGS_LOCAL_FOLDER
    uploaded = upload_local_recordings_to_drive(local_folder)
    print(f"Uploaded {len(uploaded)} file(s) from {local_folder} to the demo folder on Drive.")
