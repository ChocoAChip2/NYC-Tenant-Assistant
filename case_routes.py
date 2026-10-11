"""Owner-scoped "Your data" for a chat, and a separate, explicit form confirmation."""

import io
import logging
from flask import jsonify, render_template, request, session, url_for, send_file
import case_context
import form_review
import form_service
from supabase_service import StaleCaseError
from rate_limit import limiter
from i18n import gettext as _

logger = logging.getLogger(__name__)


def register(bp):
    from routes import get_ai_service, get_supabase_service, get_user_scoped_client

    def owned(conversation_id):
        if not session.get("user_id"):
            return None, None, (jsonify(error=_("Please log in first.")), 401)
        client = get_user_scoped_client()
        if not client:
            return None, None, (jsonify(error=_("Please log in again.")), 401)
        service = get_supabase_service()
        service.ensure_conversation_for_user(client, conversation_id, session["user_id"])
        return service, client, None

    def with_drafts(service, client, conversation_id, state):
        state["drafts"] = [{"url": url_for("main.review_form", conversation_id=conversation_id, draft_id=d["id"]),
                            "created_at": d.get("created_at", "")} for d in service.list_form_drafts(client, conversation_id)]
        return state

    def tenant_messages(service, client, conversation_id):
        return [m["content"] for m in service.fetch_messages_for_conversation(client, conversation_id)
                if m.get("role") == "user" and isinstance(m.get("content"), str)]

    @bp.route("/conversations/<conversation_id>/situation", methods=["GET", "POST"])
    @limiter.limit("60 per minute")
    def situation(conversation_id):
        """This chat's "Your data": read it, or save the tenant's own change.

        A fact the tenant changes here is theirs from then on: later
        extractions leave it alone. "clear" blanks the facts (reply
        preferences stay) and starts extraction again from the next message.
        """
        try:
            service, client, error = owned(conversation_id)
            if error:
                return error
            if request.method == "GET":
                return jsonify(with_drafts(service, client, conversation_id, service.get_case_context(client, conversation_id)))
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or type(payload.get("revision")) is not int or payload["revision"] < 0:
                return jsonify(error=_("Reload this chat and try again.")), 400
            try:
                context = case_context.validate(payload.get("context"))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            before = service.get_case_context(client, conversation_id)
            if payload.get("clear") is True:
                context, tenant_set = case_context.cleared(context), []
                seen = len(tenant_messages(service, client, conversation_id))
            else:
                tenant_set = set(before["tenant_set"]) | case_context.tenant_changes(before["context"], context)
                seen = before["seen"]
            state = service.save_case_context(client, conversation_id, session["user_id"], context,
                                              payload["revision"], tenant_set, seen)
            return jsonify(with_drafts(service, client, conversation_id, state))
        except StaleCaseError:
            return jsonify(error=_("Your data changed in another tab. Reload it before saving your changes.")), 409
        except ValueError:
            return jsonify(error=_("Conversation not found.")), 404
        except Exception:
            return jsonify(error=_("Your data could not be loaded or saved. Please try again.")), 503

    @bp.route("/conversations/<conversation_id>/situation/refresh", methods=["POST"])
    @limiter.limit("20 per minute; 300 per day")
    def refresh_situation(conversation_id):
        """Update "Your data" from the tenant's messages since the last update.

        The page calls this after a reply has arrived, so the reply never
        waits on it. It carries the chat route's own limit, and the model is
        only asked when there is a tenant message it has not read, so it can
        never make more model calls than the tenant sent messages. Only the
        tenant's messages are read: an assistant suggestion cannot become a
        fact. If the model fails or answers with something unusable, nothing
        is saved and the same messages are read again next time.
        """
        try:
            service, client, error = owned(conversation_id)
            if error:
                return error
            state = service.get_case_context(client, conversation_id)
            messages = tenant_messages(service, client, conversation_id)
            unread = messages[state["seen"]:]
            ai = get_ai_service()
            if unread and ai.is_ready():
                raw = ai.extract_case_details(case_context.extraction_input(state["context"], state["tenant_set"], unread))
                updates = case_context.parse_extracted(raw)
                if updates is not None:
                    context = case_context.apply_extracted(state["context"], state["tenant_set"], updates)
                    try:
                        state = service.save_case_context(client, conversation_id, session["user_id"], context,
                                                          state["revision"], state["tenant_set"], len(messages))
                    except StaleCaseError:
                        state = service.get_case_context(client, conversation_id)
            return jsonify(with_drafts(service, client, conversation_id, state))
        except ValueError:
            return jsonify(error=_("Conversation not found.")), 404
        except Exception:
            logger.warning("Could not update a chat's saved details.", exc_info=True)
            return jsonify(error=_("Your data could not be updated.")), 503

    @bp.route("/conversations/<conversation_id>/forms/<draft_id>", methods=["GET", "POST"])
    @limiter.limit("20 per minute")
    def review_form(conversation_id, draft_id):
        try:
            service, client, error = owned(conversation_id)
            if error:
                return error
            draft = service.get_form_draft(client, conversation_id, draft_id)
        except ValueError:
            return render_template("form_review.html", unavailable=_("This draft could not be found."), conversation_id=conversation_id), 404
        except Exception:
            return render_template("form_review.html", unavailable=_("This draft is unavailable right now. Please try again."), conversation_id=conversation_id), 503
        values = form_review.flatten(form_review.canonical(draft["intake"]))
        errors = []
        if request.method == "POST":
            intake, errors = form_review.submitted(request.form)
            values = {key: request.form.get(key, "") for key in values}
            if not errors:
                try:
                    # Saving a reviewed snapshot gives reload/re-download the exact edits.
                    # Only save when it differs; drafts are immutable and owner-scoped.
                    if intake != form_review.canonical(draft["intake"]):
                        service.create_form_draft(client, conversation_id, session["user_id"], intake)
                    pdf = form_service.fill_to_bytes(intake)
                    return send_file(io.BytesIO(pdf), as_attachment=True,
                                     download_name="RA-81_Rent_Reduction_Application.pdf", mimetype="application/pdf")
                except Exception:
                    errors = [_("Your form could not be downloaded. Your edits are still below; please try again.")]
        return render_template("form_review.html", values=values, groups=form_review.FIELDS,
                               choices=form_review.CHOICES, errors=errors,
                               created_at=draft.get("created_at", ""), conversation_id=conversation_id,
                               scope=request.form.get("service_scope", "")), 400 if errors else 200
