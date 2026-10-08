"""HTTP routes for signup, login, chat, and logout pages.

app.py registers this blueprint, and each route uses the shared SupabaseService
stored in the Flask app config to handle authentication and chat persistence.
"""

import base64
import io
import json
import time
import logging
from datetime import date
import os
from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, session, url_for, send_file

import account_requirements
import branding
import building_service
import law_service
import citation_guard
import conversation_titles
import password_safety
import profile_service
import retrieval_service
from ai_service import AIService
from markdown_service import render_markdown
from login_lockout import format_duration, record_failure, record_success, seconds_until_unlocked
from rate_limit import limiter, rate_limit_key
from supabase_service import SupabaseService
import form_service
import case_context
import form_review

# The blueprint groups the page routes together so app.py can register them as
# one unit.
main_bp = Blueprint("main", __name__)


def _safe_next(target: str | None) -> str:
    """A same-site path to continue to, or the chat page."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("main.chat")


def _token_expires_at(token: str | None) -> float | None:
    """The `exp` claim of a JWT, read without verifying it (Supabase verifies)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


# Refresh this long before the access token actually expires, so a
# request that starts just before expiry doesn't fail halfway through.
TOKEN_REFRESH_MARGIN_SECONDS = 120


@main_bp.before_request
def _keep_session_fresh():
    """Swap an expiring Supabase access token for a fresh one.

    Access tokens last an hour, but the browser session lasts much longer.
    Before this, every page that queried as the tenant returned a 500
    ("JWT expired") once a session was an hour old. If the refresh token
    is no good either, the session is cleared: protected pages then send
    the tenant to log in, and public pages carry on as logged out.
    """
    if not session.get("user_id"):
        return None
    expires_at = _token_expires_at(session.get("access_token"))
    if expires_at is None or expires_at - time.time() > TOKEN_REFRESH_MARGIN_SECONDS:
        return None
    tokens = None
    try:
        tokens = get_supabase_service().refresh_tokens(session.get("refresh_token"))
    except Exception:
        logger.warning("Could not refresh an expired session.", exc_info=True)
    if tokens:
        session["access_token"], session["refresh_token"] = tokens
        return None
    session.clear()
    flash("Your session expired. Please log in again.", "error")
    return None


@main_bp.before_request
def _ask_for_missing_account_details():
    """Send a signed-in tenant to /account/complete when their account is
    missing something a feature now needs (account_requirements.py).

    Checked once per session and requirements version: at login from the
    sign-in response, and here for sessions that were already signed in
    when a requirement was added. Only the pages in GATED_ENDPOINTS wait.
    """
    if request.endpoint not in account_requirements.GATED_ENDPOINTS or not session.get("user_id"):
        return None
    if not account_requirements.is_current(session):
        try:
            metadata = get_supabase_service().get_user_metadata(session.get("access_token"))
            account_requirements.record(session, metadata)
        except Exception:
            # Never a lock-out: if the account can't be read, carry on and
            # ask at the next login.
            logger.warning("Could not check the account's details.", exc_info=True)
            account_requirements.record_unknown(session)
    if account_requirements.pending(session):
        return redirect(url_for("main.complete_account", next=request.full_path.rstrip("?")))
    return None
logger = logging.getLogger(__name__)

# Per-message cap, in characters. Gemini calls bill (and take longer)
# roughly in proportion to input size, so this bounds both the cost and the
# latency risk of one oversized message, on top of the per-minute rate
# limit below.
#
# 4000 is deliberately in the range mainstream AI chat products settled on
# for a single turn (ChatGPT's free tier enforced ~4096 characters for
# years) rather than the 1000 first suggested: 1000 characters is roughly
# 150 words, and this app's whole job is tenants describing a situation --
# pasting two paragraphs out of an eviction notice or a lease clause blows
# past 1000 without anyone abusing anything. This is a one-line change if
# a tighter cap turns out to be wanted.
#
# chat.html reads this same constant (passed into the template) for the
# textarea's maxlength and its live character counter, so the number lives
# in exactly one place; the client-side cap is only a nicer typing
# experience, and the check in chat_message() below is the one that holds.
MAX_MESSAGE_LENGTH = 4000

# Matches the sidebar's rename <input maxlength="120"> -- the client-side
# cap is just a nicer typing experience; this is the one that actually
# holds, since the client-side value is trivial to bypass.
MAX_CONVERSATION_TITLE_LENGTH = 120

# How long a requested account deletion sits cancellable before the
# database's scheduled purge actually removes it. 30 days is the window
# Google, Discord and most consumer products settled on: long enough that
# someone who deletes in frustration, or has their account taken over, has
# a realistic chance to come back and undo it. The number is duplicated in
# exactly one other place -- the purge_after value written into
# account_deletion_requests -- and that row stores an absolute timestamp,
# so changing this never moves a deadline someone was already shown.
ACCOUNT_DELETION_GRACE_PERIOD_DAYS = 30


def get_supabase_service() -> SupabaseService:
    """Fetch the shared service object that app.py stored on the Flask app."""
    return current_app.config["SUPABASE_SERVICE"]


def get_ai_service() -> AIService:
    """Fetch the shared AI service object that app.py stored on the Flask app."""
    return current_app.config["AI_SERVICE"]


def get_user_scoped_client():
    """Build an RLS-scoped Supabase client from the session's access token.

    Returns None if there is no token or the token is no longer valid, so
    callers can send the visitor back to login instead of hitting Supabase
    with a request that RLS will just reject anyway.
    """
    access_token = session.get("access_token")
    if not access_token:
        return None

    try:
        return get_supabase_service().build_user_scoped_client(access_token)
    except Exception:
        logger.exception("Failed to build user-scoped Supabase client.")
        return None


@main_bp.route("/favicon.ico")
@limiter.exempt
def favicon():
    """The ST mark as an SVG icon. Browsers request /favicon.ico on every
    new visit; without this each one logged a 404 (2026-09-29 sweep).
    Chrome, Firefox and Safari all honor image/svg+xml here."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#1c5d8c"/>'
        '<text x="16" y="21" font-family="Arial,Helvetica,sans-serif" font-size="13" font-weight="700" '
        f'fill="#ffffff" text-anchor="middle">{branding.LOGO_MONOGRAM}</text></svg>'
    )
    response = make_response(svg)
    response.headers["Content-Type"] = "image/svg+xml"
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# One budget for the lookup at both of its URLs. Each lookup costs several
# calls to the city's APIs, so "/" and "/building" must not be two separate
# 30-a-minute allowances. HEAD counts too (Flask runs the whole view for
# it, API calls included). Only an actual lookup counts: opening the front
# page with no address calls nothing, and counting it would let a shared
# connection (a library, a tenant meeting) lock everyone out of the front
# door. Both from the 2026-09-30 review.
def _no_lookup_requested() -> bool:
    return not (request.args.get("address") or "").strip()


_building_lookup_limit = limiter.shared_limit(
    "30 per minute",
    scope="building_lookup",
    methods=["GET", "HEAD"],
    exempt_when=_no_lookup_requested,
)


@main_bp.route("/", methods=["GET", "POST"])
@_building_lookup_limit
def home():
    """The front door is the building lookup, not a signup form.

    It needs no account and it's the part of the site a general chatbot
    can't do, so a first-time visitor sees real value before being asked
    for an email address. /building keeps working (shared lookup links
    use it), and signup moved to /signup.

    A POST here can only come from a signup form rendered before the move,
    left open in a tab. 307 keeps the method and body, so it still works
    (and is still rate limited, at /signup).
    """
    if request.method == "POST":
        return redirect(url_for("main.signup"), code=307)
    return _render_building_lookup()


@main_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def signup():
    """Show the signup page and create a new account on form submission."""
    today = date.today()
    form_limits = {
        "dob_max": profile_service.latest_allowed_birthday(today).isoformat(),
        "dob_min": f"{today.year - profile_service.MAX_AGE}-01-01",
        "min_age": profile_service.MIN_AGE,
    }
    if request.method == "POST":
        supabase_service = get_supabase_service()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        # Echoed back into the form on any error so nothing has to be
        # retyped -- except the password, which is never echoed.
        entered = {
            "email": email,
            "first_name": request.form.get("first_name", ""),
            "last_name": request.form.get("last_name", ""),
            "date_of_birth": request.form.get("date_of_birth", ""),
        }

        def retry():
            return render_template("signup.html", form=entered, **form_limits)

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return retry()

        try:
            profile = profile_service.validate(
                entered["first_name"], entered["last_name"], entered["date_of_birth"], today=today
            )
        except profile_service.ProfileError as exc:
            flash(str(exc), "error")
            return retry()

        # Checked against public breach corpora (see password_safety.py).
        # Fails open by design: an unreachable API must never stop someone
        # making an account.
        if password_safety.is_breached(password):
            flash(password_safety.MESSAGE, "error")
            return retry()

        try:
            # Ask Supabase to create the account. sign_up() returns False
            # instead of raising when the email is already registered, since
            # Supabase itself won't always tell us that directly (see the
            # docstring in supabase_service.py) -- either way, we must not
            # tell the visitor to "check your email" for an account that
            # already exists and never got a new confirmation email.
            # The confirmation email's link lands on /login, where the
            # confirmed tenant can sign in, rather than on Supabase's Site
            # URL (the site root, which is now the building lookup). The
            # URL must also be in Supabase's Redirect URLs allowlist.
            created = supabase_service.sign_up(
                email=email,
                password=password,
                email_redirect_to=url_for("main.login", _external=True),
                # Encrypted before it leaves this process; None (store
                # nothing) when encryption isn't configured.
                metadata=profile_service.to_metadata(profile),
            )
            if not created:
                return render_template("signup.html", existing_account_email=email, form=entered, **form_limits)

            flash(
                "Sign-up successful. Please confirm your email, then log in.",
                "success",
            )
            return redirect(url_for("main.login"))
        except Exception as exc:
            logger.warning("Sign-up request failed (%s).", type(exc).__name__)
            flash("Sign-up failed. Check your details and try again shortly.", "error")
            return retry()

    return render_template("signup.html", form={}, **form_limits)


@main_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    """Show the login page and create a browser session after authentication.

    Failed attempts are also tracked per-caller (see login_lockout.py): ten
    wrong passwords in a row locks that caller out of this route for an
    hour, doubling on each further lockout, on top of the per-minute rate
    limit above -- the rate limit alone resets every minute, which slows a
    scripted attack but doesn't stop one from just running slowly.
    """
    if request.method == "POST":
        supabase_service = get_supabase_service()
        lockout_key = rate_limit_key()
        wait_seconds = seconds_until_unlocked(lockout_key)
        if wait_seconds > 0:
            flash(
                f"Too many failed login attempts. Try again in {format_duration(wait_seconds)}.",
                "error",
            )
            return render_template("login.html")

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("login.html")

        try:
            auth_response = supabase_service.sign_in(email=email, password=password)
            session["user_email"] = auth_response.user.email
            session["user_id"] = auth_response.user.id
            session["access_token"] = auth_response.session.access_token
            session["refresh_token"] = auth_response.session.refresh_token
            first_name = profile_service.first_name_from_metadata(
                getattr(auth_response.user, "user_metadata", None)
            )
            if first_name:
                session["first_name"] = first_name
            else:
                session.pop("first_name", None)
            record_success(lockout_key)

            # A fresh login re-checks everything and forgets earlier skips.
            session.pop(account_requirements.SKIPPED_KEY, None)
            account_requirements.record(session, getattr(auth_response.user, "user_metadata", None))
            if account_requirements.pending(session):
                return redirect(url_for("main.complete_account"))
            return redirect(url_for("main.chat"))
        except Exception as exc:
            record_failure(lockout_key)
            logger.warning("Login request failed (%s).", type(exc).__name__)
            flash(
                "Login failed. Check your email and password, and confirm your email if needed.",
                "error",
            )

    return render_template("login.html")


@main_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def forgot_password():
    """Collect an email and ask Supabase to send it a password-reset link.

    The tight rate limit here isn't just abuse-of-this-app protection: this
    route makes Supabase send an email to whatever address is submitted, so
    with no limit at all it doubles as a free tool for spamming an
    arbitrary inbox with reset-password emails, or for brute-forcing
    Supabase's own outbound email quota.
    """

    if request.method == "POST":
        email = request.form.get("email", "").strip()

        if not email:
            flash("Please enter your email.", "error")
            return render_template("forgot_password.html")

        try:
            get_supabase_service().send_password_reset_email(
                email=email,
                redirect_to=url_for("main.reset_password", _external=True),
            )
        except Exception:
            # Deliberately swallowed: whether Supabase is unreachable, the
            # email doesn't exist, or anything else goes wrong, the visitor
            # sees the same message either way -- see the docstring on
            # send_password_reset_email for why this must not reveal
            # whether an account exists for this address. Real failures
            # (e.g. Supabase misconfigured) still land in the server logs.
            logger.exception("Failed to send password reset email.")

        flash(
            "If an account exists for that email, we've sent a link to reset your password.",
            "success",
        )
        return redirect(url_for("main.login"))

    return render_template("forgot_password.html")


@main_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def reset_password():
    """Set a new password from the recovery link Supabase emailed the user.

    Supabase puts the recovery access/refresh tokens in the URL *fragment*
    (#access_token=...&refresh_token=...&type=recovery), which browsers
    never send to the server -- so reset_password.html reads them with
    JavaScript and copies them into hidden form fields before this route
    ever sees them. There is no logged-in session at this point (the
    visitor followed an emailed link), so this can't use session tokens
    the way settings.html's update_account does -- these tokens *are* the
    only proof of identity here, which is exactly how Supabase's recovery
    flow is designed to work.
    """

    if request.method == "POST":
        access_token = request.form.get("access_token", "")
        refresh_token = request.form.get("refresh_token", "")
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not access_token or not refresh_token:
            flash("This reset link is invalid or has expired. Request a new one below.", "error")
            return redirect(url_for("main.forgot_password"))

        # A typo must not cost the tenant their reset link: the tokens came
        # from the URL fragment, which the page has already cleared, so they
        # are handed back to the re-rendered form.
        def retry():
            response = make_response(render_template(
                "reset_password.html", access_token=access_token, refresh_token=refresh_token))
            response.headers["Cache-Control"] = "no-store"  # the page now carries the tokens
            return response

        if not new_password or new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return retry()

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return retry()

        # Checked against public breach corpora (see password_safety.py).
        # Fails open by design: an unreachable API must never stop someone
        # making an account.
        if password_safety.is_breached(new_password):
            flash(password_safety.MESSAGE, "error")
            return retry()

        try:
            get_supabase_service().update_account(
                access_token=access_token,
                refresh_token=refresh_token,
                password=new_password,
            )
            flash("Your password has been reset. Please log in.", "success")
            return redirect(url_for("main.login"))
        except Exception:
            logger.exception("Failed to reset password.")
            flash(
                "Could not reset your password. The link may have expired -- request a new one.",
                "error",
            )
            return redirect(url_for("main.forgot_password"))

    return render_template("reset_password.html")


@main_bp.route("/chat")
def chat():
    """Render the chat page: a conversation list plus the selected conversation."""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    supabase_service = get_supabase_service()
    conversations = supabase_service.list_conversations(user_client, archived=False)
    archived_conversations = supabase_service.list_conversations(user_client, archived=True)
    conversation_id = request.args.get("conversation_id")
    messages = []

    if conversation_id:
        try:
            supabase_service.ensure_conversation_for_user(user_client, conversation_id, session["user_id"])
            messages = supabase_service.fetch_messages_for_conversation(user_client, conversation_id)
            # Only the assistant's turns are Markdown. A tenant who types
            # "**" means "**", and rendering their own text as markup
            # would also widen the injection surface for no benefit.
            for message in messages:
                if message.get("role") == "assistant":
                    content = message.get("content") or ""
                    if citation_guard.has_markers(content):
                        # Replies stored before markers were resolved
                        # (2026-09-30): the passages they named are gone.
                        content = citation_guard.strip_citations(content)
                        message["content"] = content
                    message["content_html"] = render_markdown(content)
        except ValueError:
            flash("That conversation could not be found.", "error")
            conversation_id = None

    if conversation_id and messages and get_ai_service().is_ready():
        active = next(
            (c for c in conversations + archived_conversations if c.get("id") == conversation_id), None
        )
        _name_older_conversation(supabase_service, user_client, active, session["user_id"], messages)

    return render_template(
        "chat.html",
        user_email=session["user_email"],
        first_name=session.get("first_name"),
        ai_ready=get_ai_service().is_ready(),
        conversations=conversations,
        archived_conversations=archived_conversations,
        active_conversation_id=conversation_id,
        messages=messages,
        max_message_length=MAX_MESSAGE_LENGTH,
        form_ready_message=form_service.READY_MESSAGE,
        form_ready_html=render_markdown(form_service.READY_MESSAGE),
        situation_options=case_context.OPTIONS,
        situation_labels=case_context.LABELS,
        situation_choice_labels=case_context.CHOICE_LABELS,
        situation_limits=case_context.TEXT_LIMITS,
    )


@main_bp.route("/conversations", methods=["POST"])
@limiter.limit("20 per minute")
def create_conversation():
    """Create a new named conversation and jump straight into it."""
    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    title = request.form.get("title", "").strip()[:MAX_CONVERSATION_TITLE_LENGTH] or conversation_titles.DEFAULT_TITLE
    supabase_service = get_supabase_service()

    # An empty conversation may hold an unsent draft in another tab.
    # Only the explicit Delete action should remove a tenant's chats.

    try:
        conversation_id = supabase_service.create_conversation(user_client, session["user_id"], title)
        return redirect(url_for("main.chat", conversation_id=conversation_id))
    except Exception:
        logger.exception("Failed to create conversation.")
        flash("Could not start a new conversation. Please try again.", "error")
        return redirect(url_for("main.chat"))


@main_bp.route("/conversations/<conversation_id>/archive", methods=["POST"])
def archive_conversation(conversation_id):
    """Soft-hide a conversation from the main sidebar list without deleting it."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    try:
        get_supabase_service().set_conversation_archived(
            user_client, conversation_id, session["user_id"], archived=True
        )
        flash("Conversation archived.", "success")
    except ValueError:
        flash("That conversation could not be found.", "error")
    except Exception:
        logger.exception("Failed to archive conversation.")
        flash("Could not archive that conversation. Please try again.", "error")

    return redirect(url_for("main.chat"))


@main_bp.route("/conversations/<conversation_id>/unarchive", methods=["POST"])
def unarchive_conversation(conversation_id):
    """Move a conversation back into the main sidebar list."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    try:
        get_supabase_service().set_conversation_archived(
            user_client, conversation_id, session["user_id"], archived=False
        )
        flash("Conversation restored.", "success")
    except ValueError:
        flash("That conversation could not be found.", "error")
    except Exception:
        logger.exception("Failed to unarchive conversation.")
        flash("Could not restore that conversation. Please try again.", "error")

    return redirect(url_for("main.chat"))


@main_bp.route("/conversations/<conversation_id>/rename", methods=["POST"])
@limiter.limit("20 per minute")
def rename_conversation(conversation_id):
    """Rename a conversation. Otherwise a chat started as "New conversation"
    is named from its first message (_name_new_conversation)."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    title = request.form.get("title", "").strip()[:MAX_CONVERSATION_TITLE_LENGTH]

    if not title:
        # The conversation being renamed is presumably still right there in
        # the sidebar, so send the visitor back into it to retry rather
        # than dropping them out to the plain chat list.
        flash("Conversation name cannot be empty.", "error")
        return redirect(url_for("main.chat", conversation_id=conversation_id))

    try:
        get_supabase_service().rename_conversation(user_client, conversation_id, session["user_id"], title)
        return redirect(url_for("main.chat", conversation_id=conversation_id))
    except ValueError:
        # Unlike the blank-title case above, the conversation itself is
        # what's missing here -- redirecting back into conversation_id
        # would just make /chat's own lookup immediately repeat this same
        # flash. Land on the plain chat list instead, matching
        # archive/unarchive/delete's error handling.
        flash("That conversation could not be found.", "error")
    except Exception:
        logger.exception("Failed to rename conversation.")
        flash("Could not rename that conversation. Please try again.", "error")

    return redirect(url_for("main.chat"))


@main_bp.route("/conversations/<conversation_id>/delete", methods=["POST"])
def delete_conversation(conversation_id):
    """Permanently delete a conversation and its messages. Cannot be undone."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    try:
        get_supabase_service().delete_conversation(user_client, conversation_id, session["user_id"])
        flash("Conversation deleted.", "success")
    except ValueError:
        flash("That conversation could not be found.", "error")
    except Exception:
        logger.exception("Failed to delete conversation.")
        flash("Could not delete that conversation. Please try again.", "error")

    # If the conversation that was just deleted was the active one, this
    # redirect (with no conversation_id) naturally lands back on the
    # greeting/empty state instead of a dead link.
    return redirect(url_for("main.chat"))


def _ground_reply(user_client, question, history, context=None):
    """Retrieve law, ask for a grounded answer, and check what comes back.

    Returns (reply, source_chips). Every step except the model call itself
    is wrapped, because this whole subsystem is optional and a tenant
    asking about heat should never see an error because a vector index was
    not built. The model call is deliberately NOT wrapped: chat_message()
    already maps its ValueError/RuntimeError to the right status codes, and
    swallowing them here would only spend a second API call to fail again.

    The guard runs in REPORT mode by default: it records every violation
    and changes nothing. A guard that silently rejects good replies makes
    the assistant say less than it knows and nobody notices, so the
    false-rejection rate gets measured from these logs BEFORE
    LEGAL_GUARD_MODE is set to "enforce".
    """
    ai_service_instance = get_ai_service()
    if context is not None:
        history = [{"role": "system", "content": case_context.instructions(context)}, *history]

    passages = []
    try:
        if retrieval_service.is_enabled():
            retriever = retrieval_service.RetrievalService(
                supabase_client=user_client,
                gemini_client=getattr(ai_service_instance, "client", None),
            )
            passages = retriever.search(case_context.retrieval_query(question, context or {}))
    except Exception:
        logger.exception("Legal retrieval failed; answering without sources.")
        passages = []

    if not passages:
        # Either grounding is off, or nothing cleared the relevance floor.
        # An uncited answer is the honest outcome, not a failure. Markers
        # the history taught the model to write would point at nothing.
        return citation_guard.strip_citations(ai_service_instance.generate_reply(history)), []

    try:
        sources_block = retrieval_service.format_for_prompt(passages)
    except Exception:
        logger.exception("Could not format retrieved sources; answering without them.")
        return citation_guard.strip_citations(ai_service_instance.generate_reply(history)), []

    reply = ai_service_instance.generate_reply(
        [{"role": "system", "content": sources_block}, *history]
    )

    try:
        result = citation_guard.check(reply, passages, user_message=question)
    except Exception:
        # A guard CRASH is not a guard violation: it means nothing was
        # verified, so nothing has earned a citation. Strip them in both
        # modes. Report mode's promise is not to act on violations, and
        # this is not one.
        logger.exception("Citation guard crashed; sending the reply without citations.")
        return citation_guard.strip_citations(reply), []

    if not result.ok:
        logger.warning(
            "citation_guard: %d violation(s) [%s] mode=%s",
            len(result.violations),
            ", ".join(sorted(result.kinds)),
            _guard_mode().value,
        )
        for violation in result.violations:
            logger.warning("citation_guard: %s -- %s", violation.kind, violation.detail)

        if _guard_mode() == citation_guard.GuardMode.ENFORCE:
            # The prose may still be useful; the authority it claimed is
            # what it has not earned.
            return citation_guard.strip_citations(reply), []

    try:
        sources = citation_guard.render_sources(passages, result)
    except Exception:
        logger.exception("Could not render citation chips; sending the reply without them.")
        sources = []
    try:
        return citation_guard.resolve_markers(reply, passages), sources
    except Exception:
        logger.exception("Could not resolve citation markers; sending the reply without them.")
        return citation_guard.strip_citations(reply), sources


def _guard_mode():
    raw = os.environ.get("LEGAL_GUARD_MODE", "").strip().lower()
    return (
        citation_guard.GuardMode.ENFORCE
        if raw == citation_guard.GuardMode.ENFORCE.value
        else citation_guard.GuardMode.REPORT
    )


def _title_for(first_message, reply):
    """A title for an opening exchange, or "" (conversation_titles.py has the rules)."""
    title = ""
    try:
        title = conversation_titles.clean(get_ai_service().generate_title(first_message, reply or ""))
    except Exception:  # noqa: BLE001 -- fall back to the keyword title
        logger.warning("Title model unavailable; using a keyword title.")
    return title or conversation_titles.from_keywords(first_message)


def _name_new_conversation(supabase_service, user_client, conversation_id, user_id, history, reply):
    """Rename a conversation still called "New conversation" after its first reply.

    Returns the new title, or None when nothing changed. Best-effort by
    design: any failure here is logged and the chat reply goes out
    untouched.
    """
    try:
        user_messages = [m for m in history if m.get("role") == "user"]
        if len(user_messages) != 1:
            return None
        current = supabase_service.get_conversation_title(user_client, conversation_id, user_id)
        if not conversation_titles.is_default(current):
            return None
        title = _title_for(user_messages[0].get("content") or "", reply)
        if not title:
            return None
        supabase_service.rename_conversation(user_client, conversation_id, user_id, title)
        return title
    except Exception:  # noqa: BLE001 -- naming must never fail the chat
        logger.warning("Could not name a new conversation.", exc_info=True)
        return None


def _name_older_conversation(supabase_service, user_client, conversation, user_id, messages):
    """Chats started before auto-naming existed are named when next opened.

    Only the open conversation, only if it still has the default title and
    has a message from the tenant. Updates `conversation` in place so the
    sidebar shows the new title on this same page load.
    """
    try:
        if not conversation or not conversation_titles.is_default(conversation.get("title")):
            return
        first = next((m for m in messages if m.get("role") == "user"), None)
        if not first:
            return
        reply = next((m.get("content") or "" for m in messages if m.get("role") == "assistant"), "")
        title = _title_for(first.get("content") or "", reply)
        if title:
            supabase_service.rename_conversation(user_client, conversation["id"], user_id, title)
            conversation["title"] = title
    except Exception:  # noqa: BLE001 -- naming must never break the page
        logger.warning("Could not name an older conversation.", exc_info=True)


@main_bp.route("/chat/message", methods=["POST"])
@limiter.limit("20 per minute; 300 per day")
def chat_message():
    """Persist a user message, generate an AI reply from the stored history, and persist that too."""
    if not session.get("user_id"):
        return jsonify({"error": "Please log in first."}), 401

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        return jsonify({"error": "Your session expired. Please log in again."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    conversation_id = payload.get("conversation_id")
    content = payload.get("content")
    content = content.strip() if isinstance(content, str) else ""

    if not isinstance(conversation_id, str) or not conversation_id or not content:
        return jsonify({"error": "A conversation and message are required."}), 400

    if len(content) > MAX_MESSAGE_LENGTH:
        return jsonify(
            {"error": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)."}
        ), 400

    supabase_service = get_supabase_service()
    user_id = session["user_id"]

    try:
        supabase_service.ensure_conversation_for_user(user_client, conversation_id, user_id)
    except ValueError:
        return jsonify({"error": "Conversation not found."}), 404
    except Exception:
        logger.exception("Could not check the conversation before sending.")
        return jsonify({"error": "Could not open this conversation right now. Please try again."}), 503

    try:
        context = supabase_service.get_case_context(user_client, conversation_id)["context"]
        history = supabase_service.fetch_messages_for_conversation(user_client, conversation_id)
        # A failed model call leaves a saved, unanswered tenant turn. Reuse
        # it on retry, including after a page reload, rather than repeating
        # it in the transcript and in the model's input.
        retrying = bool(history and history[-1].get("role") == "user"
                        and history[-1].get("content") == content)
        if not retrying:
            supabase_service.insert_message(user_client, {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "user",
                "content": content,
            })
            history.append({"role": "user", "content": content})
        reply, sources = _ground_reply(
            user_client,
            content,
            [{"role": message["role"], "content": message["content"]} for message in history],
            context=context,
        )

        # The intake hand-off: the assistant answers with the RA-81 JSON
        # once the tenant has confirmed everything (ai_service.py). It is
        # checked here, not trusted: a form missing something DHCR needs is
        # never produced -- the tenant is asked for it instead.
        intake_data = form_service.parse_intake(reply)
        if intake_data is not None and intake_data.get("form", "RA-81") != "RA-81":
            # A different requested form must never silently become RA-81.
            reply = (
                "I can fill in RA-81 here, but I can't generate that other form. "
                "Let's confirm which form fits your situation first. "
                "You can find the official forms on [HCR's forms page]"
                "(https://hcr.ny.gov/tenant-owner-forms)."
            )
            sources = []
            intake_data = None
        if intake_data is not None:
            intake = form_service.normalize_intake(intake_data)
            missing = form_service.missing_fields(intake)
            if missing:
                reply = form_service.missing_message(missing)
                sources = []
            else:
                draft_id = supabase_service.create_form_draft(user_client, conversation_id, user_id, form_review.canonical(intake_data))
                review_url = url_for("main.review_form", conversation_id=conversation_id, draft_id=draft_id)
                reply = f"Your RA-81 draft is ready to review. [Review and confirm your form]({review_url}). Check every field before downloading. Nothing has been filed."
                sources = []

        supabase_service.insert_message(user_client, {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "assistant",
            "content": reply,
        })

        body = {"reply": reply, "reply_html": render_markdown(reply), "sources": sources}
        title = _name_new_conversation(supabase_service, user_client, conversation_id, user_id, history, reply)
        if title:
            body["title"] = title
        return jsonify(body)

    except ValueError:
        return jsonify({"error": "No valid messages were provided."}), 400
    except RuntimeError:
        return jsonify({"error": "The assistant is unavailable right now. Please try again later."}), 503
    except Exception:
        logger.exception("Failed to generate AI response.")
        return jsonify({"error": "The AI service is currently unavailable. Please try again shortly."}), 500


@main_bp.route("/building")
@_building_lookup_limit
def building_lookup():
    """Public: what the City of New York already knows about a building.

    Deliberately reachable without logging in. This is the first thing on
    the site that a general chatbot cannot do, so it is the front door --
    a tenant should see real value before being asked for an email address.

    GET with the address in the query string on purpose: the result is a
    shareable link ("look up our building"), and the lookup changes no
    state. The address is a building, not a person; the optional apartment
    is the most personal thing here, and it is never stored.

    Every failure renders this same page with a plain-language message.
    The city's API having a bad afternoon must not look like this site
    being broken -- but the status code still tells the truth (503 when
    the city's service is down), so monitoring can.
    """
    return _render_building_lookup()


def _render_building_lookup():
    address = (request.args.get("address") or "").strip()
    apt = (request.args.get("apt") or "").strip()
    report = None
    error = error_title = None
    status = 200

    if address:
        try:
            report = building_service.lookup(address, apt)
        except building_service.InvalidAddress as exc:
            error_title, error = "Check the address", str(exc)
            status = 400
        except building_service.AddressNotFound:
            error_title = "We couldn't find that building"
            error = (
                "No NYC building matched that address. Check the house number, "
                "and try adding the borough or ZIP code."
            )
            status = 404
        except building_service.LookupUnavailable:
            logger.warning("Building lookup unavailable for a request.", exc_info=True)
            error_title = "The city's data service didn't respond"
            error = "This is usually brief. Please try again in a minute."
            status = 503
        except Exception:
            logger.exception("Unexpected failure in building lookup.")
            error_title = "Something went wrong"
            error = "We couldn't complete that lookup. Please try again."
            status = 500

    chip_links = _law_chip_links(report) if report else {}
    chat_prompt = building_service.chat_prompt(report) if report else ""
    chat_title = (report.match.label.split(",")[0].title() if report else "")[:80]

    return render_template(
        "building.html",
        address=address,
        apt=apt,
        report=report,
        error=error,
        error_title=error_title,
        chat_prompt=chat_prompt,
        chat_title=chat_title,
        logged_in=bool(session.get("user_id")),
        max_address_length=building_service.MAX_ADDRESS_LENGTH,
        max_apartment_length=building_service.MAX_APARTMENT_LENGTH,
        max_per_class=60,
        hpd_clear_violations_url=building_service.HPD_CLEAR_VIOLATIONS_URL,
        dataset_url=building_service.HPD_VIOLATIONS_DATASET_URL,
        chip_links=chip_links,
    ), status


def _law_chip_links(report) -> dict[str, str]:
    """Chip label -> /law URL, for Admin Code sections the library holds.

    One batched query for the page (law_service.linkable_citations), and
    only unambiguous numbers are linked. Any failure means plain chips.
    """
    try:
        violations = list(report.apartment_violations or [])
        for _, _, items in report.open_by_class():
            violations.extend(items)
        numbers = {}
        for v in violations:
            for label in v.citations or []:
                number = law_service.admin_code_number(label)
                if number:
                    numbers[label] = number
        if not numbers:
            return {}
        client = getattr(get_supabase_service(), "client", None)
        linkable = law_service.linkable_citations(client, numbers.values())
        return {
            label: url_for("main.law_section", citation=number)
            for label, number in numbers.items()
            if number in linkable
        }
    except Exception:
        # Links are a bonus on this page; the violations are the point.
        logger.exception("Could not build law links for a building page.")
        return {}


@main_bp.route("/law/<citation>")
@limiter.limit("60 per minute")
def law_section(citation):
    """Public: one section of NYC or NY State law, verbatim, from the official publisher.

    Reachable without logging in, like /building: a citation has to open
    for anyone it is shown to. The text is exactly what American Legal
    Publishing publishes (loaded by tools/corpus), with its amendment
    history, the date we last checked it against the official code, and a
    link to the official page. A section that has left the code is still
    shown, with a notice, because old citations point at it.
    """
    if not law_service.is_valid_citation(citation):
        tidy = law_service.normalize_citation(citation)
        if tidy:
            return redirect(url_for("main.law_section", citation=tidy), code=301)
        abort(404)
    status = 200
    sections: list = []
    error = None
    try:
        sections = law_service.get_sections(getattr(get_supabase_service(), "client", None), citation)
    except law_service.LawUnavailable:
        logger.warning("Legal library unavailable for /law/%s.", citation, exc_info=True)
        error = "unavailable"
        status = 503
    if not sections and not error:
        error = "not_found"
        status = 404
    return render_template(
        "law.html",
        citation=citation,
        sections=sections,
        error=error,
        logged_in=bool(session.get("user_id")),
        alp_url=law_service.ALP_CODE_LIBRARY_URL,
        nys_laws_url=law_service.NYS_LAWS_URL,
    ), status


@main_bp.route("/learn-more")
def learn_more():
    """Public support/info page: what this is, what it is not, where to get
    real help.

    Deliberately reachable without logging in. Someone deciding whether to
    trust this thing with their housing situation should be able to read
    what it does and does not claim to be *before* handing over an email
    address, and someone who has already been given bad news by it should
    be able to find a real lawyer without authenticating first.
    """

    return render_template(
        "learn_more.html",
        full_disclaimer_points=branding.FULL_DISCLAIMER_POINTS,
        help_resources=branding.HELP_RESOURCES,
        logged_in=bool(session.get("user_id")),
    )


@main_bp.route("/account/complete", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def complete_account():
    """Ask a signed-in tenant for what their account is missing.

    Shows every pending requirement (account_requirements.py) on one page
    and saves them in one metadata update, through the tenant's own
    session. Never a dead end: a failed save lets them continue and asks
    again next login, and optional requirements have "Remind me next time".
    """
    if not session.get("user_id"):
        return redirect(url_for("main.login"))
    next_url = _safe_next(request.values.get("next"))
    pending = account_requirements.pending(session)
    if not pending:
        return redirect(next_url)

    today = date.today()
    page = dict(
        requirements=pending,
        next_url=next_url,
        can_skip=not any(r.required for r in pending),
        dob_max=profile_service.latest_allowed_birthday(today).isoformat(),
        dob_min=f"{today.year - profile_service.MAX_AGE}-01-01",
        min_age=profile_service.MIN_AGE,
        form={},
    )
    if request.method == "GET":
        return render_template("account_complete.html", **page)

    if request.form.get("action") == "skip":
        optional = [r.key for r in pending if not r.required]
        account_requirements.skip(session, optional)
        return redirect(next_url if not account_requirements.pending(session) else url_for("main.complete_account", next=next_url))

    entered = {key: value for key, value in request.form.items() if key not in ("csrf_token", "action", "next")}
    metadata, updates, errors = {}, {}, []
    for requirement in pending:
        try:
            collected = requirement.collect(entered, today)
        except account_requirements.RequirementError as exc:
            errors.append(str(exc))
            continue
        metadata.update(collected.metadata)
        updates.update(collected.session_updates)
    if errors:
        for message in errors:
            flash(message, "error")
        page["form"] = entered
        return render_template("account_complete.html", **page), 400

    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    try:
        tokens = get_supabase_service().update_user_metadata(access_token, refresh_token, metadata)
    except Exception:
        logger.exception("Failed to save missing account details.")
        account_requirements.skip(session, [r.key for r in pending])
        flash("We couldn't save that just now. You can carry on, and we'll ask again next time you log in.", "error")
        return redirect(next_url)

    if tokens:
        session["access_token"], session["refresh_token"] = tokens
    session.update(updates)
    account_requirements.mark_met(session, [r.key for r in pending])
    flash("Thanks, you're all set.", "success")
    return redirect(next_url)


@main_bp.route("/settings")
def settings():
    """Show the settings page: appearance, account, data export, deletion."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    # A pending deletion swaps the "Delete account" control for a banner
    # with the exact date and a cancel button. Best-effort: if this lookup
    # fails the rest of Settings still renders, since every other section
    # on the page works fine without it.
    pending_deletion = None
    user_client = get_user_scoped_client()
    if user_client:
        try:
            pending_deletion = get_supabase_service().get_pending_account_deletion(
                user_client, session["user_id"]
            )
        except Exception:
            logger.exception("Failed to look up a pending account deletion.")

    # The saved name and date of birth, to pre-fill "Your name". Also
    # best-effort: an older account has none, and a failed read just
    # shows the empty form.
    profile = None
    profile_known = True
    try:
        profile = profile_service.read_metadata(
            get_supabase_service().get_user_metadata(session.get("access_token"))
        )
    except Exception:
        profile_known = False
        logger.warning("Could not read the account profile for Settings.", exc_info=True)

    today = date.today()
    return render_template(
        "settings.html",
        user_email=session["user_email"],
        pending_deletion=pending_deletion,
        deletion_grace_period_days=ACCOUNT_DELETION_GRACE_PERIOD_DAYS,
        profile=profile or {},
        has_profile=bool(profile) or not profile_known,
        dob_max=profile_service.latest_allowed_birthday(today).isoformat(),
        dob_min=f"{today.year - profile_service.MAX_AGE}-01-01",
    )


@main_bp.route("/settings/profile", methods=["POST"])
@limiter.limit("10 per minute")
def update_profile():
    """Save the signed-in user's name and date of birth (encrypted).

    Mainly for accounts made before sign-up asked for them. Same rules and
    same encrypted envelope as sign-up (profile_service); with no
    encryption key configured nothing is saved.
    """
    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    if not access_token or not refresh_token:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    try:
        profile = profile_service.validate(
            request.form.get("first_name", ""),
            request.form.get("last_name", ""),
            request.form.get("date_of_birth", ""),
        )
    except profile_service.ProfileError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.settings") + "#profile")

    metadata = profile_service.to_metadata(profile)
    if metadata is None:
        flash("Your name can't be saved right now. Please try again later.", "error")
        return redirect(url_for("main.settings") + "#profile")

    try:
        tokens = get_supabase_service().update_user_metadata(access_token, refresh_token, metadata)
        if tokens:
            session["access_token"], session["refresh_token"] = tokens
        session["first_name"] = profile.first_name
        account_requirements.mark_met(session, [account_requirements.PROFILE.key])
        flash(f"Saved. We'll greet you as {profile.first_name}.", "success")
    except Exception:
        logger.exception("Failed to save the account profile.")
        flash("Your name couldn't be saved. Please try again.", "error")
    return redirect(url_for("main.settings") + "#profile")


@main_bp.route("/settings/account/delete", methods=["POST"])
@limiter.limit("5 per minute")
def request_account_deletion():
    """Schedule the signed-in account for deletion after a grace period.

    Deliberately gated behind two separate confirmations, both re-checked
    here rather than trusted from the page: an acknowledgement checkbox,
    and the user typing their own email address. Client-side confirmation
    is a UX nicety that a crafted POST skips entirely -- and "your account
    and every conversation in it are gone" is not a thing to do on a single
    unverified request.

    Nothing is deleted at this point. The account stays fully usable for
    the whole grace period, which is what makes the cancel route below a
    real undo rather than a formality.
    """

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    acknowledged = request.form.get("confirm_understood") == "yes"
    typed_email = request.form.get("confirm_email", "").strip().lower()
    account_email = (session.get("user_email") or "").strip().lower()

    if not acknowledged:
        flash("Tick the confirmation box to schedule your account for deletion.", "error")
        return redirect(url_for("main.settings"))

    if not typed_email or typed_email != account_email:
        flash("The email you typed does not match this account. Nothing was deleted.", "error")
        return redirect(url_for("main.settings"))

    try:
        get_supabase_service().request_account_deletion(
            user_client, session["user_id"], ACCOUNT_DELETION_GRACE_PERIOD_DAYS
        )
        flash(
            f"Your account is scheduled for deletion in {ACCOUNT_DELETION_GRACE_PERIOD_DAYS} days. "
            "You can cancel any time before then from this page.",
            "success",
        )
    except Exception:
        logger.exception("Failed to schedule an account deletion.")
        flash("Could not schedule your account for deletion. Please try again.", "error")

    return redirect(url_for("main.settings"))


@main_bp.route("/settings/account/delete/cancel", methods=["POST"])
@limiter.limit("10 per minute")
def cancel_account_deletion():
    """Cancel a pending account deletion.

    No confirmation gate on this one on purpose -- the risky direction is
    scheduling a deletion, not stopping one, so cancelling should be a
    single click.
    """

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    try:
        cancelled = get_supabase_service().cancel_account_deletion(
            user_client, session["user_id"]
        )
        if cancelled:
            flash("Your account is no longer scheduled for deletion.", "success")
        else:
            flash("There was no pending deletion to cancel.", "error")
    except Exception:
        logger.exception("Failed to cancel an account deletion.")
        flash("Could not cancel the deletion. Please try again.", "error")

    return redirect(url_for("main.settings"))


@main_bp.route("/settings/account", methods=["POST"])
@limiter.limit("10 per minute")
def update_account():
    """Update the signed-in user's email and/or password via Supabase auth."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    if not access_token or not refresh_token:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    new_email = request.form.get("email", "").strip()
    new_password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not new_email and not new_password:
        flash("Enter a new email and/or password to update your account.", "error")
        return redirect(url_for("main.settings"))

    if new_password and new_password != confirm_password:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("main.settings"))

    if new_password and len(new_password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("main.settings"))

    # Checked against public breach corpora (see password_safety.py).
    # Fails open by design: an unreachable API must never stop someone
    # changing their password.
    if new_password and password_safety.is_breached(new_password):
        flash(password_safety.MESSAGE, "error")
        return redirect(url_for("main.settings"))

    try:
        get_supabase_service().update_account(
            access_token=access_token,
            refresh_token=refresh_token,
            email=new_email or None,
            password=new_password or None,
        )
        if new_email:
            flash(f"Check {new_email} for a confirmation link before the new email takes effect.", "success")
        if new_password:
            flash("Password updated.", "success")
    except Exception as exc:
        logger.warning("Account update failed (%s).", type(exc).__name__)
        flash("Could not update account. Check your details and try again shortly.", "error")

    return redirect(url_for("main.settings"))


@main_bp.route("/settings/download-logs")
def download_chat_history():
    """Export every conversation the signed-in user has as a Markdown file."""

    if not session.get("user_id"):
        return redirect(url_for("main.login"))

    user_client = get_user_scoped_client()
    if not user_client:
        session.clear()
        flash("Your session expired. Please log in again.", "error")
        return redirect(url_for("main.login"))

    conversations = get_supabase_service().fetch_all_conversations_with_messages(user_client)
    content = _render_conversations_as_markdown(conversations)

    response = current_app.response_class(content, mimetype="text/markdown")
    slug = "-".join(branding.PRODUCT_NAME.lower().split())
    response.headers["Content-Disposition"] = f"attachment; filename={slug}-chat-history.md"
    return response


def _render_conversations_as_markdown(conversations: list[dict]) -> str:
    """Turn a list of conversations (each with a "messages" list) into one Markdown document."""

    lines = [f"# {branding.PRODUCT_NAME} -- Chat History Export", ""]

    if not conversations:
        lines.append("_No conversations yet._")

    for conversation in conversations:
        lines.append(f"## {conversation.get('title') or 'Untitled conversation'}")
        lines.append(f"_Conversation ID: {conversation['id']} -- created {conversation.get('created_at', 'unknown')}_")
        lines.append("")
        if conversation.get("situation"):
            lines.extend(["### Your situation", json.dumps(conversation["situation"], ensure_ascii=False, indent=2), ""])
        if conversation.get("form_drafts"):
            lines.extend(["### Form drafts", json.dumps(conversation["form_drafts"], ensure_ascii=False, indent=2), ""])
        for message in conversation.get("messages", []):
            speaker = "You" if message.get("role") == "user" else "Assistant"
            content = message.get("content", "") or ""
            if speaker == "Assistant" and citation_guard.has_markers(content):
                # Same as the chat page: replies stored before markers were
                # resolved carry "[S5]"s that point at nothing any more.
                content = citation_guard.strip_citations(content)
            lines.append(f"**{speaker}:** {content}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


@main_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the browser session and return the user to the login page.

    POST-only (and so CSRF-protected, like every other state-changing
    route) rather than a plain GET link -- a GET version could be forced
    on a signed-in visitor by any page that embeds e.g. <img
    src="/logout">, since browsers send GET requests for those without
    asking. Logging someone out uninvited doesn't expose or change any
    data, but there's no reason to leave a request forgery hole open once
    the fix is just a <form> instead of an <a>.
    """
    session.clear()
    return redirect(url_for("main.login"))


import case_routes
case_routes.register(main_bp)
