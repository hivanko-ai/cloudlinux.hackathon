"""
Get feature names from the Slite "Demos" table.
Opens https://cloudlinux.slite.com/app/docs/I2p-mUX0o6_rns/Demos
Extracts the "All docs" table (Feature, Date, Team, Presenter). Uses pandas if available, else stdlib.
Returns the last n values from the Feature column (n = number of recordings in the folder).
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    pd = None
    _HAS_PANDAS = False

# Local folder where recording files are stored (used to determine n)
RECORDINGS_FOLDER = r"C:\Users\elena\OneDrive\Документы\Test_for_Demo"

# Slite doc root (directory view) — we must open /Demos to get the table
SLITE_DOC_ROOT = "https://cloudlinux.slite.com/app/docs/I2p-mUX0o6_rns"
# Demos table page (must use this URL, not the root)
DEFAULT_SLITE_TABLE_URL = "https://cloudlinux.slite.com/app/docs/I2p-mUX0o6_rns/Demos"


def _ensure_demos_url(url: str) -> str:
    """Ensure URL points to the Demos table page, not the doc root."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_SLITE_TABLE_URL
    if url.endswith("/Demos"):
        return url
    if url == SLITE_DOC_ROOT or url.endswith("I2p-mUX0o6_rns"):
        return DEFAULT_SLITE_TABLE_URL
    return url if "/Demos" in url else f"{url.rstrip('/')}/Demos"


def count_recordings_in_folder(folder: str) -> int:
    """Return the number of files (recordings) in the given folder."""
    path = Path(folder)
    if not path.is_dir():
        return 0
    return sum(1 for p in path.iterdir() if p.is_file())


def _get_slite_table_url() -> str:
    load_dotenv()
    return os.environ.get("SLITE_TABLE_URL", "").strip() or DEFAULT_SLITE_TABLE_URL


def _get_project_dir() -> Path:
    return Path(__file__).resolve().parent


def _parse_table_from_page_text(text: str) -> list[dict]:
    """
    Parse the Demos table from page innerText when DOM extraction fails.
    Slite often hides column headers in another layer; the data appears as:
    row_num, Feature, Date (Mon DD, YYYY), Team, Presenter_abbrev?, Presenter...
    """
    if not text or "All docs" not in text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    date_pattern = re.compile(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}$"
    )
    rows = []
    i = 0
    while i < len(lines):
        if not date_pattern.match(lines[i]):
            i += 1
            continue
        date_str = lines[i]
        feature = lines[i - 1] if i >= 1 else ""
        team = lines[i + 1] if i + 1 < len(lines) else ""
        presenter = ""
        if i + 2 < len(lines):
            candidate = lines[i + 2]
            if len(candidate) <= 2 or candidate.isdigit():
                presenter = lines[i + 3] if i + 3 < len(lines) else candidate
            else:
                presenter = candidate
        if feature and not date_pattern.match(feature) and not feature.isdigit():
            rows.append({
                "Feature": feature,
                "Date": date_str,
                "Team": team,
                "Presenter": presenter,
            })
        i += 1
    return rows


def get_feature_names_from_slite(
    recordings_folder: str | None = None,
    slite_url: str | None = None,
) -> list[str]:
    """
    Open the Slite Demos table and return the last n values from the Feature column.

    :param recordings_folder: Folder path to count recordings (n = number of files); default RECORDINGS_FOLDER.
    :param slite_url: Slite table page URL; default from env or DEFAULT_SLITE_TABLE_URL.
    :return: List of n strings (Feature names from the last n rows of the table).
    """
    folder = (recordings_folder or RECORDINGS_FOLDER).rstrip(os.sep)
    n = count_recordings_in_folder(folder)
    if n <= 0:
        return []

    url = _ensure_demos_url(slite_url or _get_slite_table_url())

    project_dir = _get_project_dir()
    user_data_dir = project_dir / ".slite_chrome_profile"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # Open the Demos table page (not the doc root). URL must end with /Demos.
            print(f"  Opening Demos table: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            print()
            print("  >>> Browser opened. Please:")
            print("      1. Log in to Slite if you see a login page.")
            print("      2. If you see the doc root (directory), the script will open the Demos table after you press Enter.")
            print("      3. When the Demos table (Date + Feature columns) is visible, press ENTER.")
            print()
            input("  Press Enter when the Demos table is visible... ")
            # Ensure we're on the Demos table page (not the doc root)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)
            # Focus the "All docs" table section (so we read from that table, not the whole page)
            try:
                page.get_by_text("All docs", exact=True).first.click(timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                pass

            # Wait for the Demos "All docs" table (Feature, Date, Team, Presenter) to be present.
            try:
                page.wait_for_selector('text="Feature"', timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_selector('text="Presenter"', timeout=3000)
            except Exception:
                pass

            # Extract the full "All docs" table (only this table, not the whole page).
            # JS: from "Feature" header find the table, get column indices for Feature, Date, Team, Presenter, return all rows as list of { Feature, Date, Team, Presenter }.
            full_table_rows = []
            extract_full_table_js = """
                (el) => {
                    if (!el) return [];
                    const trim = (s) => (s || '').trim();
                    const headerNames = ['Feature', 'Date', 'Team', 'Presenter'];
                    let row = el;
                    while (row && row !== document.body) {
                        const par = row.parentElement;
                        if (!par) break;
                        const siblings = Array.from(par.children);
                        if (siblings.length >= 2) {
                            const indices = {};
                            for (let i = 0; i < siblings.length; i++) {
                                const t = trim(siblings[i].textContent);
                                if (t === 'Feature' || t.indexOf('Feature') === 0) indices['Feature'] = i;
                                if (t.indexOf('Date') >= 0) indices['Date'] = i;
                                if (t === 'Team' || t.indexOf('Team') === 0) indices['Team'] = i;
                                if (t === 'Presenter' || t.indexOf('Presenter') === 0) indices['Presenter'] = i;
                            }
                            if (indices['Feature'] !== undefined) {
                                const table = par.parentElement;
                                if (table && table.children.length > 1) {
                                    const allRows = Array.from(table.children);
                                    const dataRows = allRows.filter(r => r !== par);
                                    return dataRows.map(r => ({
                                        Feature: indices['Feature'] !== undefined && r.children[indices['Feature']] ? trim(r.children[indices['Feature']].textContent) : '',
                                        Date: indices['Date'] !== undefined && r.children[indices['Date']] ? trim(r.children[indices['Date']].textContent) : '',
                                        Team: indices['Team'] !== undefined && r.children[indices['Team']] ? trim(r.children[indices['Team']].textContent) : '',
                                        Presenter: indices['Presenter'] !== undefined && r.children[indices['Presenter']] ? trim(r.children[indices['Presenter']].textContent) : ''
                                    })).filter(x => x.Feature.length > 0);
                                }
                            }
                        }
                        row = par;
                    }
                    return [];
                }
            """
            for frame in page.frames:
                for exact in (True, False):
                    try:
                        loc = frame.get_by_text("Feature", exact=exact)
                        loc.first.wait_for(state="visible", timeout=2000)
                        full_table_rows = loc.first.evaluate(extract_full_table_js, None)
                        if isinstance(full_table_rows, list) and len(full_table_rows) > 0:
                            break
                    except Exception:
                        full_table_rows = []
                        continue
                if isinstance(full_table_rows, list) and len(full_table_rows) > 0:
                    break

            if not isinstance(full_table_rows, list):
                full_table_rows = []

            # Fallback: Slite often doesn't expose "Feature" in the same DOM as the table.
            # Table may be virtualized — scroll to bottom so last rows are in the DOM, then parse innerText.
            if not full_table_rows:
                try:
                    # Scroll to bottom so virtualized table loads last rows (59–67). Try both window and scrollable divs.
                    def _scroll_to_bottom():
                        for _ in range(20):
                            page.evaluate("""
                                () => {
                                    window.scrollTo(0, document.body.scrollHeight);
                                    document.querySelectorAll('[style*="overflow"], [class*="scroll"]').forEach(el => {
                                        if (el.scrollHeight > el.clientHeight) {
                                            el.scrollTop = el.scrollHeight;
                                        }
                                    });
                                }
                            """)
                            page.wait_for_timeout(400)
                    _scroll_to_bottom()
                    page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
                    full_table_rows = _parse_table_from_page_text(str(page_text))
                    if not full_table_rows:
                        for frame in page.frames:
                            try:
                                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                frame_text = frame.evaluate("() => document.body ? document.body.innerText : ''")
                                full_table_rows = _parse_table_from_page_text(str(frame_text))
                                if full_table_rows:
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

            # Build table and show df.head() so you can see the table we saved.
            last_n_texts = []
            if full_table_rows:
                if _HAS_PANDAS:
                    df = pd.DataFrame(full_table_rows)
                    print("\n  df.head() — table saved from Demos (All docs):")
                    print(df.head().to_string())
                    last_n_texts = df.tail(n)["Feature"].astype(str).tolist()
                else:
                    # No pandas: print first 5 rows so you see the table we saved (install pandas for df.head()).
                    print("\n  df.head() — table saved from Demos (first 5 rows; install pandas for DataFrame):")
                    for i, row in enumerate(full_table_rows[:5], 1):
                        print(f"    {i}  Feature: {row.get('Feature', '')}  |  Date: {row.get('Date', '')}  |  Team: {row.get('Team', '')}  |  Presenter: {row.get('Presenter', '')}")
                    last_n_texts = [r["Feature"] for r in full_table_rows[-n:] if r.get("Feature")]
            else:
                print("\n  df.head() — no table rows extracted (table empty or not found).")

            # Strategy 2: Position-based — only if full table extraction returned nothing.
            if not last_n_texts:
                for frame in page.frames:
                    try:
                        loc = frame.get_by_text("Feature", exact=True)
                        loc.first.wait_for(state="visible", timeout=2000)
                        box = loc.first.bounding_box()
                        if not box:
                            continue
                        col_left = box["x"] - 30
                        col_right = box["x"] + box["width"] + 30
                        below_top = box["y"] + box["height"] + 5
                        result = frame.evaluate(
                            """
                            ([colLeft, colRight, belowTop, n]) => {
                                const candidates = [];
                                document.querySelectorAll('*').forEach(el => {
                                    const t = (el.textContent || '').trim();
                                    if (!t || t === 'Feature') return;
                                    const r = el.getBoundingClientRect();
                                    if (r.height < 1 || r.width < 1 || r.top < belowTop) return;
                                    const mid = (r.left + r.right) / 2;
                                    if (mid < colLeft || mid > colRight) return;
                                    candidates.push({ top: r.top, text: t });
                                });
                                candidates.sort((a, b) => a.top - b.top);
                                const rowHeight = 20;
                                const rowTexts = [];
                                let lastTop = -999;
                                for (const c of candidates) {
                                    if (c.top - lastTop > rowHeight) {
                                        rowTexts.push(c.text);
                                    } else {
                                        const prev = rowTexts[rowTexts.length - 1] || '';
                                        rowTexts[rowTexts.length - 1] = c.text.length > prev.length ? c.text : prev;
                                    }
                                    lastTop = c.top;
                                }
                                return rowTexts.slice(-n);
                            }
                            """,
                            [col_left, col_right, below_top, n],
                        )
                        if isinstance(result, list) and len(result) > 0:
                            last_n_texts = result
                            break
                    except Exception:
                        continue

            # Strategy 3: Pure JS (shadow DOM, normalized text) if still nothing.
            if not last_n_texts:
                extract_js = """
                    (n) => {
                        const featureLabel = 'Feature';
                        function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
                        function findFeatureElement(root) {
                            root = root || document.body;
                            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
                            let el;
                            while (el = walker.nextNode()) {
                                if (norm(el.textContent) === featureLabel) return el;
                                if (el.shadowRoot) {
                                    const found = findFeatureElement(el.shadowRoot);
                                    if (found) return found;
                                }
                            }
                            return null;
                        }
                        const fe = findFeatureElement();
                        if (!fe) return [];
                        const rect = fe.getBoundingClientRect();
                        const colLeft = rect.left - 30;
                        const colRight = rect.right + 30;
                        const belowTop = rect.bottom + 5;
                        const candidates = [];
                        const all = document.querySelectorAll('*');
                        all.forEach(el => {
                            const t = norm(el.textContent);
                            if (!t || t === featureLabel) return;
                            const r = el.getBoundingClientRect();
                            if (r.top < belowTop || r.width < 1 || r.height < 1) return;
                            const mid = (r.left + r.right) / 2;
                            if (mid < colLeft || mid > colRight) return;
                            candidates.push({ top: r.top, text: t });
                        });
                        candidates.sort((a, b) => a.top - b.top);
                        const rowHeight = 15;
                        const rowTexts = [];
                        let lastTop = -999;
                        for (const c of candidates) {
                            if (c.top - lastTop > rowHeight) {
                                rowTexts.push(c.text);
                            } else {
                                const prev = rowTexts[rowTexts.length - 1];
                                rowTexts[rowTexts.length - 1] = prev.length >= c.text.length ? prev : c.text;
                            }
                            lastTop = c.top;
                        }
                        return rowTexts.slice(-n);
                    }
                """
                for frame in page.frames:
                    try:
                        result = frame.evaluate(extract_js, n)
                        if isinstance(result, list) and len(result) > 0:
                            last_n_texts = result
                            break
                    except Exception:
                        continue

            if not isinstance(last_n_texts, list):
                last_n_texts = []

            if not last_n_texts:
                try:
                    debug_text = page.evaluate("() => document.body ? document.body.innerText : ''")
                    debug_path = _get_project_dir() / "slite_page_debug.txt"
                    debug_path.write_text(
                        str(debug_text)[:8000] or "(empty)",
                        encoding="utf-8",
                    )
                    print(f"  Debug: page text saved to {debug_path.name} (first 8000 chars)")
                except Exception:
                    pass

            # Ensure we return exactly n strings (pad with empty if needed, or truncate)
            result = [str(x) for x in last_n_texts][-n:]
            while len(result) < n:
                result.insert(0, "")
            return result[:n]
        finally:
            if context is not None:
                context.close()


# List of feature names from last run (populated by get_feature_names_from_slite / main).
# Use this or get_feature_names_from_slite() return value from other modules.
feature_names: list[str] = []


def get_feature_names_list() -> list[str]:
    """Return the list of extracted feature names (last n rows from Slite). Uses feature_names if set, else reads feature_names.txt."""
    global feature_names
    if feature_names:
        return list(feature_names)
    path = _get_project_dir() / "feature_names.txt"
    if path.is_file():
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return []


def main() -> list[str]:
    """Run extraction and print result; returns the list of feature names (last n rows from Demos table)."""
    global feature_names
    names = get_feature_names_from_slite()
    feature_names = names
    n = count_recordings_in_folder(RECORDINGS_FOLDER)
    print(f"Recordings in folder: {n}")
    print(f"\nLast {n} rows of the table (Feature column):")
    if names:
        for i, name in enumerate(names, 1):
            print(f"  {i}. {name}")
        out_file = _get_project_dir() / "feature_names.txt"
        out_file.write_text("\n".join(names), encoding="utf-8")
        print(f"List saved to: {out_file}")
    else:
        print("(No feature names extracted — check that the Demos table with 'Feature' column is visible.)")
    return names


if __name__ == "__main__":
    main()
