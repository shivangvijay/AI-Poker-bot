import argparse
import os
import re
import sys
import time
import subprocess
from urllib.parse import urlparse, unquote
from playwright.sync_api import sync_playwright

BASE_URL = "https://aipoker.cmudsc.com"
DASHBOARD_URL = f"{BASE_URL}/dashboard?page="
DEBUG_PORT = 9222


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape poker match CSVs from the CMU AI Poker dashboard."
    )
    parser.add_argument(
        "--bot-version",
        required=True,
        help="Only download matches played by this bot version (e.g. Submission_v26)",
    )
    parser.add_argument(
        "--pages", type=int, default=10,
        help="Total number of dashboard pages to scan (default: 10)",
    )
    parser.add_argument(
        "--start-page", type=int, default=1,
        help="Page number to start from (default: 1)",
    )
    parser.add_argument(
        "--output-dir", default="poker_logs_filtered",
        help="Directory to save downloaded CSVs (default: poker_logs_filtered)",
    )
    return parser.parse_args()


def launch_chrome_with_debugging():
    """Launch real Chrome with remote debugging enabled."""
    chrome_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]

    chrome_path = None
    for path in chrome_paths:
        if os.path.exists(path):
            chrome_path = path
            break

    if not chrome_path:
        print("ERROR: Could not find Chrome. Please provide the path manually.")
        chrome_path = input("Chrome path: ").strip()
        if not os.path.exists(chrome_path):
            print("Invalid path. Exiting.")
            sys.exit(1)

    user_data = os.path.abspath("chrome_debug_profile")
    os.makedirs(user_data, exist_ok=True)

    print(f"Launching Chrome with remote debugging on port {DEBUG_PORT}...")
    subprocess.Popen([
        chrome_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={user_data}",
        f"{BASE_URL}/dashboard",
    ])
    time.sleep(3)


BUTTON_TYPES = [
    {"selector": 'button[title="Open match CSV"]',     "suffix": "",          "label": "CSV"},
    {"selector": 'button[title="Open game log (.log)"]', "suffix": "_handinfo", "label": "Game"},
]


def click_and_save(page, context, button, fallback_name, suffix, output_dir):
    """Click a button, capture the new tab content, save to file."""
    with context.expect_page(timeout=15_000) as new_page_info:
        button.click()
    new_tab = new_page_info.value
    new_tab.wait_for_load_state("load", timeout=15_000)

    tab_url = new_tab.url
    match = re.search(r'(match_\d+)', unquote(tab_url))
    file_name = f"{match.group(1)}{suffix}.csv" if match else fallback_name
    file_path = os.path.join(output_dir, file_name)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        new_tab.close()
        return file_name, True

    pre = new_tab.query_selector("pre")
    csv_text = pre.inner_text() if pre else new_tab.evaluate("document.body.innerText")

    saved = False
    if csv_text and csv_text.strip():
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(csv_text)
        saved = True
    elif tab_url and tab_url != "about:blank":
        resp = page.request.get(tab_url)
        if resp.ok:
            with open(file_path, "wb") as f:
                f.write(resp.body())
            saved = True

    new_tab.close()
    return file_name, saved


def scrape_page(page, context, page_num, bot_version, output_dir):
    """Scrape a single dashboard page, only downloading rows matching bot_version."""
    url = f"{DASHBOARD_URL}{page_num}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    rows = page.query_selector_all("table tbody tr")
    if not rows:
        print(f"  No table rows found on page {page_num}")
        return 0, 0, 0

    total_rows = len(rows)
    matched_rows = 0
    downloaded = 0

    for row_idx, row in enumerate(rows):
        cells = row.query_selector_all("td")
        if len(cells) < 4:
            continue

        version_text = cells[3].inner_text().strip()
        if version_text != bot_version:
            continue

        matched_rows += 1
        opponent = cells[2].inner_text().strip() if len(cells) > 2 else "?"

        for btype in BUTTON_TYPES:
            btn = row.query_selector(btype["selector"])
            if not btn:
                continue

            fallback = f"match_p{page_num}_r{row_idx}{btype['suffix']}.csv"
            try:
                file_name, saved = click_and_save(
                    page, context, btn, fallback, btype["suffix"], output_dir
                )
                if saved:
                    downloaded += 1
                    print(f"    [{btype['label']}] {file_name} (vs {opponent})")
                else:
                    print(f"    Empty content for {file_name}")
                time.sleep(0.3)
            except Exception as e:
                print(f"    Failed {btype['label']} row {row_idx}: {e}")
                for p in context.pages[1:]:
                    try:
                        p.close()
                    except Exception:
                        pass

    return downloaded, matched_rows, total_rows


def main():
    args = parse_args()
    bot_version = args.bot_version
    total_pages = args.pages
    start_page = args.start_page
    output_dir = args.output_dir

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("STEP 1: Chrome will open automatically.")
    print("=" * 60)
    launch_chrome_with_debugging()

    print()
    print("=" * 60)
    print("STEP 2: In the Chrome window that just opened:")
    print("  1. Pass the Cloudflare 'Verify you are human' check")
    print("  2. Log in to your account")
    print("  3. Make sure you can see the dashboard with CSV links")
    print("=" * 60)
    print()
    input("Press ENTER here once you can see the dashboard with CSV links...")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        csv_check = page.query_selector_all('button[title="Open match CSV"]')
        if not csv_check:
            print("\nWARNING: No CSV buttons detected on current page.")
            print("Make sure you are on the dashboard and can see CSV buttons.")
            answer = input("Continue anyway? (y/n): ").strip().lower()
            if answer != "y":
                browser.close()
                return

        total_csvs = 0
        total_matched = 0
        total_scanned = 0
        failed_pages = []

        print(f"\nScraping pages {start_page}-{total_pages} from {BASE_URL}/dashboard")
        print(f"Filter: bot version = '{bot_version}'")
        print(f"Saving CSVs to ./{output_dir}/\n")

        for page_num in range(start_page, total_pages + 1):
            print(f"[{page_num}/{total_pages}] Processing page {page_num}...")
            try:
                count, matched, scanned = scrape_page(
                    page, context, page_num, bot_version, output_dir
                )
                total_csvs += count
                total_matched += matched
                total_scanned += scanned
                if matched > 0:
                    print(f"  Matched {matched}/{scanned} rows, downloaded {count} file(s)")
                else:
                    print(f"  No '{bot_version}' rows on this page ({scanned} rows scanned)")
            except Exception as e:
                print(f"  Error on page {page_num}: {e}")
                failed_pages.append(page_num)

            time.sleep(0.5)

        print("\n" + "=" * 60)
        print(f"SUMMARY")
        print(f"  Bot version filter:  {bot_version}")
        print(f"  Pages scanned:       {start_page} to {total_pages}")
        print(f"  Total rows scanned:  {total_scanned}")
        print(f"  Rows matched:        {total_matched}")
        print(f"  Files downloaded:    {total_csvs}")
        print(f"  Output directory:    ./{output_dir}/")
        if failed_pages:
            print(f"  Pages with errors:   {failed_pages}")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    main()
