"""Where usage is counted: one hook after each request, one click endpoint,
and the privacy page that says what is counted.

Everything here turns a request into (event, value-from-a-fixed-list) pairs
for usage_metrics.record(). It reads the route NAME and the response
STATUS, never the URL, the query string's contents or anything typed.
The few values taken from the request (borough, a law section number, a
chip id) only count if they are on the allowlist.

A browser sending Global Privacy Control is not counted at all.
"""

import logging
from urllib.parse import urlsplit

from flask import render_template, request, session

import area_service
import branding
import law_service
import usage_metrics
from rate_limit import limiter

logger = logging.getLogger(__name__)

_SEARCH = ("google.", "bing.", "duckduckgo.", "yahoo.", "ecosia.", "search.brave.", "startpage.")
_SOCIAL = ("facebook.", "instagram.", "t.co", "twitter.", "x.com", "reddit.", "tiktok.", "linkedin.",
           "whatsapp.", "nextdoor.", "threads.")
_BY_STATUS = {
    "area": {200: "found", 404: "no_match", 400: "invalid"},
    "building": {200: "found", 404: "not_found", 400: "invalid"},
}


def opted_out() -> bool:
    return request.headers.get("Sec-GPC", "").strip() == "1"


def _device() -> str:
    agent = request.headers.get("User-Agent", "")
    return "mobile" if any(mark in agent for mark in ("Mobi", "Android", "iPhone", "iPad")) else "desktop"


def _language() -> str:
    first = request.headers.get("Accept-Language", "").split(",")[0].split(";")[0].strip().lower()
    primary = first.split("-")[0]
    return primary if primary in usage_metrics.LANGUAGES and primary != "other" else "other"


def _referrer() -> str:
    host = (urlsplit(request.headers.get("Referer", "")).hostname or "").lower()
    if not host:
        return "direct"
    if host == (request.host.split(":")[0]).lower():
        return "internal"
    bare = host[4:] if host.startswith("www.") else host
    if any(bare.startswith(name) or f".{name}" in f".{bare}" for name in _SEARCH):
        return "search"
    if any(bare == name or bare.startswith(name) or f".{name}" in f".{bare}" for name in _SOCIAL):
        return "social"
    return "other"


def _yes(value) -> str:
    return "yes" if value else "no"


def _lookup_kind(endpoint) -> str | None:
    """Which search a request was, if any: 'area', 'building' or None."""
    if endpoint not in ("main.home", "main.area_lookup", "main.building_lookup"):
        return None
    if endpoint != "main.area_lookup" and (request.args.get("address") or "").strip():
        return "building"
    if endpoint != "main.building_lookup" and (request.args.get("street") or "").strip():
        return "area"
    return None


def count_request(response) -> None:
    record = usage_metrics.record
    endpoint = request.endpoint
    status = response.status_code
    method = request.method
    page = usage_metrics.PAGES.get(endpoint)
    lookup = _lookup_kind(endpoint) if method == "GET" else None
    if lookup:
        page = lookup

    if method == "GET" and status == 200 and page and response.mimetype == "text/html":
        record("page_view", page)
        record("view_device", _device())
        record("view_language", _language())
        record("view_referrer", _referrer())
        record("view_signed_in", _yes(session.get("user_id")))
    if status >= 400:
        record("error_route", page or "other")
        record("error_status", str(status) if str(status) in usage_metrics.STATUS_CODES else "other")

    if lookup == "area":
        record("area_search", _BY_STATUS["area"].get(status, "error"))
        try:
            record("area_borough", area_service.borough_from(request.args.get("borough")))
        except area_service.InvalidArea:
            pass
        record("area_cross_used", _yes((request.args.get("cross") or "").strip()))
        record("area_zip_used", _yes((request.args.get("zip") or "").strip()))
    elif lookup == "building":
        record("building_lookup", _BY_STATUS["building"].get(status, "error"))

    if method == "GET":
        if endpoint == "main.law_section" and status == 200:
            record("law_view", str((request.view_args or {}).get("citation") or ""))
        if endpoint == "main.review_form" and status == 200:
            record("ra81", "reviewed")
        if endpoint == "main.signup" and status == 200:
            record("signup", "view")
        if endpoint in ("main.home", "main.resources"):
            record("chip_click", request.args.get("chip") or "")
    elif method == "POST":
        if endpoint == "main.chat_message":
            if status == 200 and response.is_json:
                record("chat", "message_sent")
                if (response.get_json(silent=True) or {}).get("sources"):
                    record("chat", "reply_with_citations")
            elif status in (500, 503):
                record("ai_error", "unavailable" if status == 503 else "failed")
        elif endpoint == "main.create_conversation" and status == 302 and "conversation_id=" in (response.location or ""):
            record("chat", "conversation_created")
            record("chip_click", request.form.get("chip") or "")
        elif endpoint == "main.archive_conversation" and status == 302:
            record("chat", "conversation_archived")
        elif endpoint == "main.delete_conversation" and status == 302:
            record("chat", "conversation_deleted")
        elif endpoint == "main.review_form" and response.mimetype == "application/pdf":
            record("ra81", "downloaded")
        elif endpoint == "main.signup" and status == 302:
            record("signup", "success")
        elif endpoint == "main.complete_account" and status == 302:
            record("account_complete", "skipped" if request.form.get("action") == "skip" else "saved")
        elif endpoint == "main.login" and status == 200:
            record("login", "failure")


def _same_site() -> bool:
    """A click report must come from one of our own pages.

    This is the request-forgery check for the one endpoint that has no
    token: a token needs a session cookie, and the Resources page is read
    by people who are not signed in and get no cookie from us at all.
    Browsers state where a POST came from; anything else is ignored.
    """
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site == "same-origin"
    origin = request.headers.get("Origin")
    return bool(origin) and urlsplit(origin).netloc == request.host


def register(bp):
    from routes import get_supabase_service

    @bp.after_request
    def _count_usage(response):
        try:
            if not opted_out():
                count_request(response)
            usage_metrics.flush_if_due(getattr(get_supabase_service(), "client", None))
        except Exception as exc:  # noqa: BLE001 -- counting must never break a page
            logger.warning("Usage counting failed (%s).", type(exc).__name__)
        return response

    @bp.route("/usage/click", methods=["POST"])
    @limiter.limit("60 per minute")
    def usage_click():
        """Count a click on an outbound help link. The server never sees
        those otherwise. Takes an id from a fixed list and nothing else;
        always answers 204, whether or not anything was counted."""
        if not opted_out() and _same_site():
            usage_metrics.record("resource_click", (request.form.get("id") or "")[: usage_metrics.MAX_NAME_LENGTH])
        return "", 204

    @bp.route("/privacy")
    def privacy():
        """Public: what is counted, what never is, how long it is kept."""
        return render_template(
            "privacy.html",
            title=branding.PRIVACY_TITLE,
            lede=branding.PRIVACY_LEDE,
            sections=branding.PRIVACY_SECTIONS,
            account_title=branding.PRIVACY_ACCOUNT_TITLE,
            account_text=branding.PRIVACY_ACCOUNT_TEXT,
            logged_in=bool(session.get("user_id")),
        )

    @bp.app_template_global()
    def usage_id(name):
        return usage_metrics.slug(name)
