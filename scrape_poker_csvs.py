import os
import re
import sys
import time
import subprocess
from urllib.parse import urlparse, unquote
from playwright.sync_api import sync_playwright

BASE_URL = "https://aipoker.cmudsc.com"
DASHBOARD_URL = f"{BASE_URL}/dashboard?page="
TOTAL_PAGES = 210
OUTPUT_DIR = "poker_logs"
DEBUG_PORT = 9222


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


def click_and_save(page, context, button, fallback_name, suffix):
    """Click a button, capture the new tab content, save to file, return filepath or None."""
    with context.expect_page(timeout=15_000) as new_page_info:
        button.click()
    new_tab = new_page_info.value
    new_tab.wait_for_load_state("load", timeout=15_000)

    tab_url = new_tab.url
    match = re.search(r'(match_\d+)', unquote(tab_url))
    file_name = f"{match.group(1)}{suffix}.csv" if match else fallback_name
    file_path = os.path.join(OUTPUT_DIR, file_name)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        new_tab.close()
        return file_name, True  # already existed

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


def scrape_page(page, context, page_num):
    url = f"{DASHBOARD_URL}{page_num}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    downloaded = 0
    any_buttons_found = False

    for btype in BUTTON_TYPES:
        buttons = page.query_selector_all(btype["selector"])
        if not buttons:
            continue
        any_buttons_found = True

        for idx in range(len(buttons)):
            fallback = f"match_p{page_num}_{idx}{btype['suffix']}.csv"
            try:
                btns = page.query_selector_all(btype["selector"])
                if idx >= len(btns):
                    print(f"    {btype['label']} button {idx} gone, skipping")
                    continue

                file_name, saved = click_and_save(
                    page, context, btns[idx], fallback, btype["suffix"]
                )
                if saved:
                    downloaded += 1
                else:
                    print(f"    Empty content for {file_name}")
                time.sleep(0.3)
            except Exception as e:
                print(f"    Failed {btype['label']} #{idx}: {e}")
                for p in context.pages[1:]:
                    try:
                        p.close()
                    except Exception:
                        pass

    if not any_buttons_found:
        print(f"  No buttons found on page {page_num}")

    return downloaded


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: Launch Chrome
    print("=" * 60)
    print("STEP 1: Chrome will open automatically.")
    print("=" * 60)
    launch_chrome_with_debugging()

    # Step 2: Wait for user to pass Cloudflare and log in
    print()
    print("=" * 60)
    print("STEP 2: In the Chrome window that just opened:")
    print("  1. Pass the Cloudflare 'Verify you are human' check")
    print("  2. Log in to your account")
    print("  3. Make sure you can see the dashboard with CSV links")
    print("=" * 60)
    print()
    input("Press ENTER here once you can see the dashboard with CSV links...")

    # Step 3: Connect to the browser and scrape
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
        failed_pages = []

        print(f"\nScraping {TOTAL_PAGES} pages from {BASE_URL}/dashboard")
        print(f"Saving CSVs to ./{OUTPUT_DIR}/\n")

        for page_num in range(1, TOTAL_PAGES + 1):
            print(f"[{page_num}/{TOTAL_PAGES}] Processing page {page_num}...")
            try:
                count = scrape_page(page, context, page_num)
                if count == 0:
                    failed_pages.append(page_num)
                else:
                    print(f"  Downloaded {count} CSV(s)")
                total_csvs += count
            except Exception as e:
                print(f"  Error on page {page_num}: {e}")
                failed_pages.append(page_num)

            time.sleep(0.5)

        print("\n" + "=" * 60)
        print(f"Done! Downloaded {total_csvs} files (CSV + Game) to ./{OUTPUT_DIR}/")
        if failed_pages:
            print(f"Pages with no CSVs or errors: {failed_pages}")
        print("=" * 60)

        browser.close()


if __name__ == "__main__":
    main()
