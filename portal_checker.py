"""
ABUAD Portal Result Checker
Automatically checks for the target level result release at set intervals.

Logs into https://portal.abuad.edu.ng and checks the Result History
table for a row where the Level column reads your target level. When detected,
a loud sound alert is played repeatedly until acknowledged.

Usage:
    python portal_checker.py

Press Ctrl+C to stop the script at any time.
"""

import requests
from bs4 import BeautifulSoup
import time
import winsound
import sys
import os
import threading
from datetime import datetime
from urllib.parse import quote

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# -------------------- Configuration --------------------
PORTAL_HOME = "https://portal.abuad.edu.ng/PortalHome"
LOGIN_URL = "https://portal.abuad.edu.ng/login.php"
RESULTS_URL = "https://portal.abuad.edu.ng/ResultDetails"

import getpass
print("\n=== ABUAD Portal Login ===")
USERNAME = input("Enter your Portal Username: ").strip()
PASSWORD = getpass.getpass("Enter your Portal Password: ")
print("\n=== Telegram Notifications ===")
print("To get your Chat ID: Message @userinfobot on Telegram")
TELEGRAM_CHAT_ID = input("Enter your Telegram Chat ID (or press Enter to skip): ").strip()

CHECK_INTERVAL_HOURS = 1
TARGET_LEVEL = "500"

# Telegram notification setup:
# The BOT_TOKEN stays the same so anyone can use the same bot.
TELEGRAM_BOT_TOKEN = "8293518784:AAGtfdxUQ-L-Pikq2TXWcoMyyVWJh4XM1ik"

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_log.txt")
# -------------------------------------------------------


def log(message: str):
    """Print and log a timestamped message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {message}"
    print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def send_telegram_notification(message: str):
    """Send a Telegram notification via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("  [SKIP] Telegram not configured (bot token or chat ID missing).")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        log("Sending Telegram notification...")
        resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code == 200:
            log("  [OK] Telegram notification sent successfully!")
            return True
        else:
            log(f"  [FAIL] Telegram API returned status {resp.status_code}: {resp.text[:200]}")
            return False

    except requests.exceptions.RequestException as e:
        log(f"  [FAIL] Telegram notification failed: {e}")
        return False


def play_alert():
    """Play a loud repeating alert sound until the user presses Enter."""
    log(f"[ALERT] Results with Level {TARGET_LEVEL} have been detected!")
    log("[ALERT] Press Enter to silence the alarm...")
    
    stop_event = threading.Event()

    def beep_loop():
        while not stop_event.is_set():
            # Play a high-pitched beep (1500 Hz) for 800ms, pause 400ms
            winsound.Beep(1500, 800)
            time.sleep(0.4)
            winsound.Beep(2000, 400)
            time.sleep(0.2)
            winsound.Beep(1800, 600)
            time.sleep(0.5)

    beep_thread = threading.Thread(target=beep_loop, daemon=True)
    beep_thread.start()
    input()  # Wait for the user to press Enter
    stop_event.set()
    beep_thread.join(timeout=3)
    log("Alarm silenced.")


def create_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    return session


def login(session: requests.Session) -> bool:
    """Log into the ABUAD portal. Returns True on success."""
    try:
        # Step 1: Visit the home page to get session cookies (PHPSESSID)
        log("Visiting portal home page...")
        home_resp = session.get(PORTAL_HOME, timeout=30)
        home_resp.raise_for_status()
        log(f"  -> Home page loaded (status {home_resp.status_code})")

        # Step 2: Send login POST request
        log("Sending login request...")
        login_data = {
            "userdentification": USERNAME,
            "passworduser": PASSWORD,
        }
        login_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": PORTAL_HOME,
            "Origin": "https://portal.abuad.edu.ng",
        }

        login_resp = session.post(
            LOGIN_URL,
            data=login_data,
            headers=login_headers,
            timeout=30,
        )
        login_resp.raise_for_status()

        # Check if login was successful by looking at the response
        resp_text = login_resp.text.strip().lower()
        log(f"  -> Login response (raw): {login_resp.text.strip()[:200]}")


        # If the response redirects us or contains success indicators
        if "error" in resp_text or "invalid" in resp_text or "incorrect" in resp_text:
            log("  [FAIL] Login appears to have failed.")
            return False

        log("  [OK] Login successful!")
        return True

    except requests.exceptions.RequestException as e:
        log(f"  [FAIL] Network error during login: {e}")
        return False


def check_results(session: requests.Session) -> dict:
    """
    Check the Result History page for a Level 500 entry.
    Returns a dict with:
        - 'found': bool
        - 'first_level': str or None (the level value in row 1)
        - 'rows': list of dicts with row data
    """
    result = {"found": False, "first_level": None, "rows": []}

    try:
        log("Fetching results page...")
        resp = session.get(RESULTS_URL, timeout=30)
        resp.raise_for_status()
        log(f"  -> Results page loaded (status {resp.status_code})")

        # Check if we were redirected back to login (session expired)
        if "PortalHome" in resp.url or "login" in resp.url.lower():
            log("  [FAIL] Session expired -- redirected to login page.")
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the Result History table
        tables = soup.find_all("table")
        target_table = None

        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "level" in headers and "session" in headers:
                target_table = table
                break

        if not target_table:
            # Try looking for table by column content patterns
            for table in tables:
                first_row = table.find("tr")
                if first_row:
                    cells = first_row.find_all(["th", "td"])
                    cell_text = [c.get_text(strip=True).lower() for c in cells]
                    if any("level" in t for t in cell_text):
                        target_table = table
                        break

        if not target_table:
            log("  [FAIL] Could not find the Result History table on the page.")
            log(f"  -> Page title: {soup.title.string if soup.title else 'N/A'}")
            # Log a snippet for debugging
            body_text = soup.get_text()[:500]
            log(f"  -> Page snippet: {body_text}")
            return result

        # Parse table rows (skip header row)
        rows = target_table.find_all("tr")
        data_rows = []

        for row in rows:
            # Each data row has: th (NO) + td (Session) + td (Semester) + td (Level) + td (Action)
            cells = row.find_all(["th", "td"])
            if len(cells) >= 5:
                # Skip the header row (all cells are <th>)
                if all(c.name == "th" for c in cells):
                    continue
                row_data = {
                    "no": cells[0].get_text(strip=True),
                    "session": cells[1].get_text(strip=True),
                    "semester": cells[2].get_text(strip=True),
                    "level": cells[3].get_text(strip=True),
                }
                data_rows.append(row_data)

        result["rows"] = data_rows

        if data_rows:
            first_level = data_rows[0]["level"]
            result["first_level"] = first_level
            log(f"  -> First row: Session={data_rows[0]['session']}, "
                f"Semester={data_rows[0]['semester']}, Level={first_level}")

            if first_level == TARGET_LEVEL:
                result["found"] = True
                log(f"  [FOUND] Level {TARGET_LEVEL} detected in first row!")
            else:
                log(f"  -> Level is {first_level}, waiting for {TARGET_LEVEL}...")
        else:
            log("  -> No data rows found in the table.")

    except requests.exceptions.RequestException as e:
        log(f"  [FAIL] Network error fetching results: {e}")

    return result


def print_banner():
    """Print a startup banner."""
    tg_status = "Enabled" if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) else "Not configured"
    
    lines = [
        f"Student:    {USERNAME}",
        f"Checking:   Level {TARGET_LEVEL} in Result History",
        f"Interval:   Every {CHECK_INTERVAL_HOURS} hour(s)",
        f"Alert:      Sound + Telegram ({tg_status})"
    ]
    
    banner = (
        "\n"
        "  +------------------------------------------------------------+\n"
        "  |          ABUAD Portal Result Checker                       |\n"
        "  +------------------------------------------------------------+\n"
    )
    
    for line in lines:
        banner += f"  |  {line:<56}  |\n"
        
    banner += (
        "  +------------------------------------------------------------+\n"
        "  |  Press Ctrl+C at any time to stop                          |\n"
        "  +------------------------------------------------------------+\n"
    )
    print(banner)


def run_check() -> bool:
    """
    Perform one full check cycle (login + check results).
    Returns True if Level 500 was found.
    """
    log("=" * 60)
    log("Starting check cycle...")
    log("=" * 60)

    session = create_session()

    # Step 1: Login
    if not login(session):
        log("Login failed. Will retry next cycle.")
        return False

    # Small delay to let session settle
    time.sleep(2)

    # Step 2: Check results
    result = check_results(session)

    if result["found"]:
        return True

    # Log all current rows for reference
    if result["rows"]:
        log("  Current result history:")
        for row in result["rows"]:
            log(f"    {row['no']}. {row['session']} | {row['semester']} | Level {row['level']}")

    return False


def main():
    print_banner()
    log("Script started.")
    log(f"Log file: {LOG_FILE}")
    log(f"Will check every {CHECK_INTERVAL_HOURS} hour(s).")
    print()

    check_count = 0

    try:
        while True:
            check_count += 1
            log(f"--- Check #{check_count} ---")

            found = run_check()

            if found:
                log("")
                log(f"*** RESULTS ARE OUT! Level {TARGET_LEVEL} detected! ***")
                log("Go check your portal: https://portal.abuad.edu.ng")
                log("")

                # Send Telegram notification
                send_telegram_notification(
                    "🎓 *ABUAD RESULTS ARE OUT!*\n\n"
                    f"Level {TARGET_LEVEL} results have been detected "
                    "in your Result History!\n\n"
                    "🔗 Check now: https://portal.abuad.edu.ng"
                )

                play_alert()

                # After the alert, ask if the user wants to continue checking
                response = input("\nContinue checking? (y/n): ").strip().lower()
                if response != "y":
                    log("User chose to stop. Exiting.")
                    break
                else:
                    log("User chose to continue checking.")

            else:
                current_time = datetime.now().strftime("%I:%M %p")
                # Send Telegram notification that results are not out
                send_telegram_notification(
                    f"🔄 *ABUAD Portal Update*\n\n"
                    f"Check #{check_count} completed at {current_time}.\n"
                    f"Level {TARGET_LEVEL} results are *NOT* out yet."
                )

                next_check = datetime.now().timestamp() + (CHECK_INTERVAL_HOURS * 3600)
                next_check_time = datetime.fromtimestamp(next_check).strftime("%H:%M:%S")
                log(f"Next check at: {next_check_time}")
                log(f"Sleeping for {CHECK_INTERVAL_HOURS} hour(s)...")
                print()

                # Sleep in small increments so Ctrl+C is responsive
                total_seconds = CHECK_INTERVAL_HOURS * 3600
                elapsed = 0
                while elapsed < total_seconds:
                    time.sleep(min(60, total_seconds - elapsed))
                    elapsed += 60

    except KeyboardInterrupt:
        print()
        log("Script stopped by user (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
