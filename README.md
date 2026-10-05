## Automatic re-login (new)

Direct Top Hat email/password login is supported. Enable it for an interactive run:

```sh
TOPHAT_AUTO_LOGIN=true python main.py run
```

Enter your email and password when prompted. The password is hidden and both values stay in process memory; they are not saved by the program. Set `TOPHAT_AUTO_LOGIN=true` in `.env` to enable this on every run. For unattended services, provide `TOPHAT_EMAIL` and `TOPHAT_PASSWORD` through the service environment or explicitly add them to your private `.env` (which stores them in plaintext). `TOPHAT_SCHOOL` defaults to Michigan State University. Do not send credentials in chat.

On session expiry during an active watch window, the watcher uses the direct Top Hat login form, verifies the logged-in course UI, and resumes detection. Terminal and JSON logs report login attempts and recovery without credentials. A failed recovery uses the existing session-expired notification. Attempts are shared across courses and limited to once per 15 minutes, including across restarts; after three failures in a process, use manual `--login` and restart. Window-end tab closure also interrupts login. No recovery runs outside watch windows. Manual login, discovery and `--check` do not automatically submit credentials.

The public Email/Password/Login UI was inspected; successful credential submission still needs testing with your account. Unexpected redirects, verification challenges or changed forms fall back to manual login. The watcher never enters attendance codes. Set `TOPHAT_AUTO_LOGIN=false` to disable automatic login.

The original setup guide below predates this optional feature; references to manual-only login apply when automatic login is disabled.

# Top Hat attendance watcher

A Python 3.11+ notification-only watcher. It opens your configured courses during weekly windows, observes attendance signals, and sends a link for you to use yourself. It never fills a field, clicks an attendance control, enters a code, or submits attendance. MSU SSO and Duo are completed manually.

**Setup requires your course URLs, notifier credentials, and a discovery capture.** The sample network endpoint, JSON schema, and DOM selectors are synthetic, not verified Top Hat internals. The program refuses to run with `configured: false` so placeholder rules cannot silently report that attendance is closed. No live MSU/Top Hat session or real notification delivery was used to validate this project.

## Setup

Run commands from this project directory. On macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp config.example.yaml config.yaml
cp detection.example.yaml detection.yaml
cp .env.example .env
```

On Windows, use `py -m venv .venv`, then `.venv\Scripts\Activate.ps1`, and `Copy-Item` instead of `cp`. On Linux, Playwright may require OS packages: `python -m playwright install --with-deps chromium`. Do installation under the same user that runs the service.

Edit `config.yaml` with the exact course URLs from your browser. Set `attendance_url` to the direct attendance page you want in the alert; omitted values default to `course_url`. If the attendance UI is only available on a separate page, use that page as `course_url` too. Course names must be unique and should remain stable because they identify saved alerts. `.env` holds notification credentials only, never an MSU/Top Hat password.

## Manual login

```sh
python main.py --login
```

A visible Chromium window opens. Complete MSU SSO and Duo yourself, verify you can see the course, then close every window of that browser. Its session is stored in `profile/`. The watcher uses the same persistent Chromium profile, following [Playwright's persistent context API](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).

Stop the watcher/service before `--login`, `--discover`, or `--check`; concurrent profile use is locked. The profile contains sensitive session data. Do not commit it or share it. A VPS needs a graphical desktop/remote display for manual login and discovery; copying profiles between computers is not a reliable authentication workflow.

## Discover the actual attendance signal

1. Stop the service. Run `python main.py --discover Anatomy` shortly before attendance opens. This explicitly requested diagnostic mode works outside the schedule.
2. Keep the course page open as attendance becomes available. Interact manually if you need to navigate to attendance. The tool itself only reads the page. Close the browser or press Ctrl+C when finished.
3. Look in `discovery/<UTC timestamp>/network.jsonl`. Each line has a UTC timestamp, URL (query/fragment removed), method, status, and `json_body`, a JSON-encoded string truncated to 12,000 characters. Non-JSON responses have `null`. WebSocket JSON frames have method `WEBSOCKET`. `dom-*.html` files snapshot the DOM every 10 seconds.
4. Compare responses just before and after opening. Identify a course-specific attendance response, the collection path, an attendance item ID, and the field/value that means **code entry is currently open**. A course list, historical attendance record, or Attendance navigation link alone is insufficient. Ensure the URL pattern does not also match other courses or unrelated records. Paths use dot-separated keys and `*` for list/dictionary children; they are a small traversal syntax, not full JSONPath.
5. Replace the synthetic rule in `detection.yaml`. For the included synthetic fixture, `data.items.*` traverses each item; `state` is matched against `[open, active]`, and `id` provides deduplication. Configure a rule that recognizes closed/empty attendance responses as well. An empty array at the parent of a final `.*` is recognized as a valid closed signal. No recognized network shape is treated as an error, not a negative attendance result.
6. Optionally configure DOM rules for an input or container visible **only** while entry is open. `selector` is a Playwright CSS selector, `text_pattern` is an optional Python regular expression applied to that element's text, and `id_attribute` is an optional ID-bearing attribute. `strategy: both` unions network and DOM results; `dom` uses DOM only. Prefer network when its shape is stable.
7. Set the optional `session.authenticated_selector` to a reliable logged-in course marker, update login URL patterns/selectors if needed, and set `configured: true`.
8. Run `python main.py --validate`, then `python main.py --check Anatomy` while attendance is closed and again while it is open. Check prints results only and never sends notifications. Verify a second item has a different ID.

Discovery redacts common secret keys in JSON and removes URL queries, but DOM files and arbitrary fields can still contain personal information or tokens. Keep captures local, review/redact before sharing, and delete them when no longer needed. A large/truncated response may require narrowing the page activity or inspecting browser developer tools manually. The sample fixture in `tests/fixtures/attendance.json` is synthetic.

## Notifications

Choose `NOTIFIER` in `.env`, configure its variables, then run `python main.py --test-notify`. Environment variables override `.env`; restart after changing notifier credentials.

- **ntfy** (`NOTIFIER=ntfy`): subscribe in the ntfy phone app to `NTFY_TOPIC` on `NTFY_SERVER`, then set those values. Set `NTFY_TOKEN` for an authenticated server/topic. ntfy sends push notifications, not carrier SMS. Public ntfy topics can be read by anyone who knows their name: use a random name and access-controlled topics when available. See [ntfy publishing documentation](https://docs.ntfy.sh/publish/).
- **Pushover** (`NOTIFIER=pushover`): install the app, obtain your user key and create an application API token; set `PUSHOVER_USER` and `PUSHOVER_TOKEN`. See [Pushover's API](https://pushover.net/api).
- **Twilio SMS** (`NOTIFIER=twilio`): set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, and `TWILIO_TO` (phone numbers in E.164 format). US Twilio local numbers sending SMS to US numbers require A2P 10DLC registration; trial-account and destination restrictions may also apply. See [Twilio's registration guide](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc). ntfy/Pushover are usually the quickest way to get phone notifications working without SMS registration.
- **SMTP gateway** (`NOTIFIER=smtp`): set host, port, sender, recipient, username/password as needed. `SMTP_SECURITY=starttls` defaults to port 587; `ssl` defaults to 465. Set `SMTP_TO` to your carrier's currently supported email-to-SMS address. Verify support with the carrier first; availability, delivery speed, and message length vary. Plaintext SMTP is not supported.

Alerts say `Top Hat attendance OPEN: <Course name> – <attendance_url>`. A successful provider response means accepted for delivery, not confirmed handset receipt. Each send gets three attempts, with 2- and 4-second backoff. Failed sends are logged and remain eligible on later polls. No test message is sent during project tests.

SQLite records `(course name, item ID)` when an ID is available, otherwise `(course name, window date/start/end)`. Distinct IDs alert independently in the same window. Without IDs, only one attendance alert per window is possible. Repeated/overlapping windows share fallback suppression when simultaneously active. Renaming a course, editing a window, or switching rules between ID/no-ID matching may cause another alert. Avoid combining an ID-less DOM rule with an ID-bearing network rule if both describe the same item.

A crash or timeout between provider acceptance and the SQLite commit can cause a duplicate. Exactly-once delivery cannot be guaranteed across these providers; the implementation favors retrying a potentially missed alert. Only one local watcher can use the profile/state at a time.

## Schedule and recovery behavior

`python main.py run` runs until stopped. `--headed` shows its browser for debugging. Configuration and detection rules are re-read each cycle; course additions, removals, changes, and disabled flags take effect without restart. YAML parse/validation errors close the browser, log a cycle error, and retry the files. Keep an eye on the rotating logs while editing rules.

Windows are start-inclusive/end-exclusive. An end before the start means overnight. Skipping the start date cancels the whole window; skipping the following date cuts an overnight window off at midnight. Time zone defaults to `America/Detroit` using IANA/DST rules, independent of the computer's current zone. During a fall-back repeated hour, the earliest start and latest end are used. Nonexistent spring-forward times shift forward by the DST gap; a resulting empty window is omitted.

The browser starts when a window becomes active. **Session verification runs at the window's beginning before the detection result is used**, rather than before the scheduled start: checking earlier would violate the no-requests-outside-windows requirement. Extend a window's start if you want lead time. Verification checks login redirects, visible password/login UI, and an optional authenticated marker. An expired session sends `Top Hat session expired – run --login`, at most once successfully per course/window, and is logged. Nothing attempts SSO or Duo automatically.

Each active course has one reusable tab. It gently reloads, allows `settle_ms` for XHR/fetch/WebSocket events, verifies the session, and detects attendance. Poll intervals are at least 30 seconds, measured after each completed pass; sequential course processing and network delays can make intervals longer. Poll failures back off exponentially up to 15 minutes. The watcher continues after page errors; browser failures are recovered. A timer closes each course tab at its window end, including while another course or a notification is waiting. With no active windows, the browser is closed and the process sleeps; it makes no scheduled Top Hat requests. Explicit `--login`, `--check`, and `--discover` intentionally bypass schedules.

`notify_errors: true` sends one successful detection-error notification per course/window. Startup/configuration failures appear in logs/console; an OS service restarts process crashes. If the machine sleeps, is offline, or the page changes in a way the configured rules cannot detect, alerts can be missed. The machine must stay awake with network access. DOM-only absence cannot reliably distinguish closed attendance from an unrecognized page; configure the authenticated marker and periodically recheck rules.

## Commands

```sh
python main.py run
python main.py run --headed
python main.py --login
python main.py --discover "Anatomy"
python main.py --check "Anatomy"
python main.py --test-notify
python main.py --list
python main.py --validate
python -m unittest discover -s tests -v
```

`--list` prints active and upcoming windows through the next seven days with local UTC offsets. `--validate` validates config and configured detection rules, not credentials or live selectors. Optional `--config`, `--detection`, `--profile`, and `--state` flags override their paths. Paths and `.env` are relative to the working directory. JSON logs are in `logs/watcher.jsonl` with five 2 MB backups. Each completed poll logs `open`, `not open`, or `error`; error types are logged without provider credentials.

## macOS launchd

Replace every `/ABSOLUTE/PROJECT` in `services/com.local.tophat-watcher.plist` with your project path. Create `logs/`, copy the plist to `~/Library/LaunchAgents/`, and load it:

```sh
mkdir -p logs ~/Library/LaunchAgents
cp services/com.local.tophat-watcher.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.tophat-watcher.plist
```

This LaunchAgent starts at user login and restarts after exit; it does not start before login at machine boot. For unattended boot, an administrator can install a LaunchDaemon in `/Library/LaunchDaemons`, add `UserName` for your normal user, and use the `system` domain. Do not run Chromium as root. Keep manual browser commands in your logged-in graphical session, with the service stopped. To stop the LaunchAgent before login/discovery:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.local.tophat-watcher.plist
```

Run `bootstrap` again afterward. Service stdout/stderr files are separate from the rotating application log; rotate them with your system tools if needed.

## Linux systemd

The provided file is a user unit. Replace `/ABSOLUTE/PROJECT`, copy it to `~/.config/systemd/user/`, then:

```sh
systemctl --user daemon-reload
systemctl --user enable --now tophat-watcher.service
```

For startup at boot without interactive login, an administrator must enable lingering for your account: `sudo loginctl enable-linger "$USER"`. Stop with `systemctl --user stop tophat-watcher` before manual login/discovery, and start it afterward. View service output with `journalctl --user -u tophat-watcher`. Set up and authenticate the browser on that same machine/user before enabling the unit.

## Windows Task Scheduler

Create a task under the same user who performed `--login`. Use **At startup** (or **At log on** if preferred), **Run whether user is logged on or not** for headless watching, and these action fields:

- Program: `C:\ABSOLUTE\PROJECT\.venv\Scripts\python.exe`
- Arguments: `C:\ABSOLUTE\PROJECT\main.py run` (quote the script path if it contains spaces)
- Start in: `C:\ABSOLUTE\PROJECT`

Under Settings, restart on failure every minute, set a generous retry count, allow manual start, select **Do not start a new instance**, and disable **Stop the task if it runs longer than…**. Adjust battery/sleep conditions so the computer remains available. End the task before manual login/discovery and run those commands in an interactive desktop session. Start the task again afterward.
