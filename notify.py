"""
notify.py — Notification emails sent through Resend.

  • send_arrivals()     → the receivers (NOTIFY_EMAILS): "new products are ready"
  • send_confirmation() → whoever emailed the links: what was added / failed
"""

import html
import logging
import os
from email.utils import parseaddr
from typing import List, Optional

import requests

import catalog

log = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "MARO.SHOPPING <noreply@notify.e-dmm.com>")
NOTIFY_EMAILS  = [
    a.strip()
    for a in os.environ.get(
        "NOTIFY_EMAILS",
        os.environ.get("NOTIFY_EMAIL", "fashion@notify.e-dmm.com,pjariwala@episolve.com"),
    ).split(",")
    if a.strip()
]
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
PORTFOLIO_BASE_URL = (
    os.environ.get("PORTFOLIO_BASE_URL")
    or (f"https://{_railway_domain}" if _railway_domain else "")
).rstrip("/")


def bare_address(addr: Optional[str]) -> str:
    """'MARO <a@b.com>' → 'a@b.com' (lower-cased); '' if there is no address."""
    return parseaddr(addr or "")[1].lower()


def is_receiver(addr: str) -> bool:
    return bare_address(addr) in {bare_address(a) for a in NOTIFY_EMAILS}


def is_own_address(addr: str) -> bool:
    """True for our own From address — used to ignore our own mail coming back in."""
    return bare_address(addr) == bare_address(FROM_EMAIL)


def portfolio_link(handle: str = "") -> str:
    return f"{PORTFOLIO_BASE_URL}/?product={handle}" if handle else f"{PORTFOLIO_BASE_URL}/"


def absolute_url(url: str) -> str:
    return url if url.startswith("http") else f"{PORTFOLIO_BASE_URL}{url}"


# ── Sending ──────────────────────────────────────────────────────────────────
def send(to: List[str], subject: str, html_body: str, kind: str) -> bool:
    """Send one email via Resend; the result is recorded in the catalog log."""
    error = None
    if not RESEND_API_KEY:
        error = "RESEND_API_KEY not set"
    elif not PORTFOLIO_BASE_URL:
        error = "PORTFOLIO_BASE_URL not set — links would be broken"
    else:
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": html_body},
                timeout=15,
            )
            if resp.status_code not in (200, 201):
                error = f"Resend {resp.status_code}: {resp.text[:300]}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    if error:
        log.error("%s email to %s failed: %s", kind, to, error)
    else:
        log.info("%s email sent to %s — %s", kind, to, subject)
    catalog.log_notification({"kind": kind, "to": to, "subject": subject, "ok": not error, "error": error})
    return not error


def send_arrivals(added: List[dict], failed: List[str]) -> bool:
    """Tell the receivers about new products. *added* items come from product_item()."""
    if not added:
        return False
    first = added[0]["display_name"]
    more = f" + {len(added) - 1} more" if len(added) > 1 else ""
    subject = f"New arrivals: {first}{more} — view & download"
    intro = (
        f"{len(added)} new product{'s are' if len(added) != 1 else ' is'} ready in your "
        "MARO.SHOPPING portfolio — view the images, or download them one by one or as a ZIP."
    )
    body = _paragraph(intro) + _product_rows(added) + _failed_block(failed)
    body += _button(portfolio_link(added[0]["handle"] if len(added) == 1 else ""), "Open Portfolio →")
    return send(NOTIFY_EMAILS, subject, _layout("✦ New product images are ready", body), "arrivals")


def send_confirmation(sender: str, added: List[dict], existing: List[dict], failed: List[str]) -> bool:
    """Reply to the person who emailed the links with what happened to each one."""
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if existing:
        parts.append(f"{len(existing)} already in portfolio")
    if failed:
        parts.append(f"{len(failed)} failed")
    subject = "Received: " + ", ".join(parts)

    body = _paragraph("Thanks — your email was received and processed.")
    if added:
        body += _heading("Added") + _product_rows(added)
    if existing:
        body += _heading("Already in the portfolio") + _product_rows(existing)
    body += _failed_block(failed)
    body += _button(portfolio_link(), "Open Portfolio →")
    return send([sender], subject, _layout("✦ Your products were processed", body), "confirmation")


# ── HTML pieces (inline styles — email clients ignore <style>) ──────────────
def _layout(banner: str, body: str) -> str:
    return f"""
    <div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;">
      <div style="background:#1a1a1a;padding:32px 40px;">
        <h1 style="color:#f5f0e8;font-weight:300;font-size:28px;letter-spacing:0.1em;margin:0;">MARO.SHOPPING</h1>
        <p style="color:#c4a992;font-size:10px;letter-spacing:0.22em;text-transform:uppercase;margin:6px 0 0;">Product Image Portfolio</p>
      </div>
      <div style="background:#8b6f5e;padding:14px 40px;">
        <span style="color:white;font-size:13px;letter-spacing:0.06em;">{html.escape(banner)}</span>
      </div>
      <div style="background:#f5f0e8;padding:32px 40px;">{body}</div>
      <div style="background:#1a1a1a;padding:16px 40px;">
        <p style="color:#6b6560;font-size:11px;margin:0;letter-spacing:0.04em;">This is an automated notification from your MARO.SHOPPING image pipeline.</p>
      </div>
    </div>
    """


def _paragraph(text: str) -> str:
    return f'<p style="color:#6b6560;font-size:14px;line-height:1.7;margin:0 0 24px;">{html.escape(text)}</p>'


def _heading(text: str) -> str:
    return (f'<p style="color:#6b6560;font-size:11px;text-transform:uppercase;letter-spacing:0.14em;'
            f'margin:8px 0 10px;">{html.escape(text)}</p>')


def _product_rows(items: List[dict]) -> str:
    rows = []
    for it in items:
        link = html.escape(portfolio_link(it["handle"]))
        thumb = (f'<img src="{html.escape(absolute_url(it["thumb_url"]))}" width="72" height="96" alt="" '
                 f'style="display:block;object-fit:cover;background:#e8ddd0;">') if it.get("thumb_url") else ""
        count = it["image_count"]
        rows.append(f"""
        <tr>
          <td width="84" style="padding:0 12px 12px 0;vertical-align:top;"><a href="{link}">{thumb}</a></td>
          <td style="padding:0 0 12px;vertical-align:middle;">
            <a href="{link}" style="font-size:18px;color:#1a1a1a;text-decoration:none;">{html.escape(it["display_name"])}</a>
            <div style="color:#6b6560;font-size:12px;margin-top:4px;">{count} image{'s' if count != 1 else ''}</div>
          </td>
        </tr>""")
    return f'<table cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px;">{"".join(rows)}</table>'


def _failed_block(failed: List[str]) -> str:
    if not failed:
        return ""
    items = "".join(
        f'<li style="margin:0 0 4px;word-break:break-all;">{html.escape(u)}</li>' for u in failed
    )
    return (_heading("Could not download")
            + f'<ul style="color:#c0392b;font-size:12px;margin:0 0 24px;padding-left:18px;">{items}</ul>')


def _button(href: str, label: str) -> str:
    return (f'<a href="{html.escape(href)}" style="display:inline-block;background:#1a1a1a;color:#f5f0e8;'
            f'padding:15px 36px;text-decoration:none;font-size:12px;letter-spacing:0.14em;'
            f'text-transform:uppercase;">{html.escape(label)}</a>')
