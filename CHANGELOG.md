# Changelog

## 2026-10-16 — v5: usability, notifications, automatic cleanup

Triggered by user feedback: *"I don't get notifications, also can we fix it so that new items show on top. It also has a glitch that when I am scrolling it randomly starts over at the top."*

### Why it was broken
- **Scroll jump:** the page reloaded and re-drew everything every 30 seconds, resetting the scroll position and "Load more".
- **Order:** the sidebar and slider re-sorted products alphabetically by their random alias names.
- **No notifications:** `PORTFOLIO_BASE_URL` was missing on Railway (every email link was broken), and notifications were sent from and to the same inbound address (`fashion@notify.e-dmm.com`), so they looped back into the system instead of reaching a person.

### Fixed / added
- **Stable page:** no automatic re-drawing. A "✦ N new products — tap to show" button appears instead.
- **Newest first** everywhere, grouped Today / Yesterday / This week / This month, "✦ New" badges since your last visit, A–Z toggle. Thumbnails use the first photo (no longer random).
- **Notifications:** one "New arrivals" email per incoming email (with thumbnails) to `fashion@notify.e-dmm.com` and `pjariwala@episolve.com`, from `MARO.SHOPPING <noreply@notify.e-dmm.com>`; plus a "Received" confirmation to the sender (what was added / already there / failed). Last email status shown in the portfolio.
- **Automatic cleanup** (daily, ~6am New York): sold-out/removed or 30-day-old products → Archived; archived 30 days → deleted; back-in-stock items restored. New **Portfolio / 📦 Archived** tabs at the top of the page with a highlighted rule banner, "Archives in N days" warnings, and ↺ restore buttons.
- **Security:** Resend webhook signature check (`RESEND_WEBHOOK_SECRET`), passcode enforced by the server (before, delete buttons worked for anyone with the link), duplicate-webhook and email-loop guards, file name validation.
- **Layout fixes:** desktop sidebar stays in view while scrolling; mobile menu no longer opens behind the header; typing in search no longer closes the mobile menu.
- **Code:** new `storage.py`, `catalog.py` (product records, `products.json`), `notify.py`, `maintenance.py`. Local image copies are removed once uploaded to R2.

### Railway settings changed
- Added `PORTFOLIO_BASE_URL`, `RESEND_WEBHOOK_SECRET`.
- Removed `NOTIFY_EMAIL`, `FROM_EMAIL` (new defaults in code).

### Commits
- `2bd19c9` Upgrade portfolio: stable scrolling, newest first, notifications, auto-cleanup
- `aa74dba` Make Archived tab and auto-cleanup rule prominent

### Still open
- Confirm in Resend → Inbound that `fashion@notify.e-dmm.com` is the address that receives links. If so, its copy of "New arrivals" is ignored by the loop guard and only `pjariwala@episolve.com` actually receives it — add another receiver via `NOTIFY_EMAILS` if needed.
- Replace the GitHub token stored in this repo's git remote URL.
- Legacy `name_map.json` (alias → real product handle) is still publicly readable at the R2 bucket root.
