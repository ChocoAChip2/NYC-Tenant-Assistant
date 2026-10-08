"""Owner-scoped situation editing and a separate, explicit form confirmation."""

import io
from flask import jsonify, render_template, request, session, url_for, send_file
import case_context
import form_review
import form_service
from supabase_service import StaleCaseError
from rate_limit import limiter


def register(bp):
    from routes import get_supabase_service, get_user_scoped_client

    def owned(conversation_id):
        if not session.get("user_id"):
            return None, None, (jsonify(error="Please log in first."), 401)
        client = get_user_scoped_client()
        if not client:
            return None, None, (jsonify(error="Please log in again."), 401)
        service = get_supabase_service()
        service.ensure_conversation_for_user(client, conversation_id, session["user_id"])
        return service, client, None

    @bp.route("/conversations/<conversation_id>/situation", methods=["GET", "POST"])
    @limiter.limit("60 per minute")
    def situation(conversation_id):
        try:
            service, client, error = owned(conversation_id)
            if error:
                return error
            if request.method == "GET":
                state = service.get_case_context(client, conversation_id)
                state["drafts"] = [{"url": url_for("main.review_form", conversation_id=conversation_id, draft_id=d["id"]),
                                    "created_at": d.get("created_at", "")} for d in service.list_form_drafts(client, conversation_id)]
                return jsonify(state)
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or type(payload.get("revision")) is not int or payload["revision"] < 0:
                return jsonify(error="Reload your situation and try again."), 400
            try:
                context = case_context.validate(payload.get("context"))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            return jsonify(service.save_case_context(client, conversation_id, session["user_id"], context, payload["revision"]))
        except StaleCaseError:
            return jsonify(error="This situation changed in another tab. Reload it before saving your changes."), 409
        except ValueError:
            return jsonify(error="Conversation not found."), 404
        except Exception:
            return jsonify(error="Your situation could not be loaded or saved. Please try again."), 503

    @bp.route("/conversations/<conversation_id>/forms/<draft_id>", methods=["GET", "POST"])
    @limiter.limit("20 per minute")
    def review_form(conversation_id, draft_id):
        try:
            service, client, error = owned(conversation_id)
            if error:
                return error
            draft = service.get_form_draft(client, conversation_id, draft_id)
        except ValueError:
            return render_template("form_review.html", unavailable="This draft could not be found.", conversation_id=conversation_id), 404
        except Exception:
            return render_template("form_review.html", unavailable="This draft is unavailable right now. Please try again.", conversation_id=conversation_id), 503
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
                    errors = ["Your form could not be downloaded. Your edits are still below; please try again."]
        return render_template("form_review.html", values=values, groups=form_review.FIELDS,
                               choices=form_review.CHOICES, errors=errors,
                               created_at=draft.get("created_at", ""), conversation_id=conversation_id,
                               scope=request.form.get("service_scope", "")), 400 if errors else 200
