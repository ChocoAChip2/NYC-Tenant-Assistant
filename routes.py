from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from supabase_service import SupabaseService

main_bp = Blueprint("main", __name__)


def get_supabase_service() -> SupabaseService:
    return current_app.config["SUPABASE_SERVICE"]


@main_bp.route("/", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        supabase_service = get_supabase_service()

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("signup.html")

        try:
            supabase_service.sign_up(email=email, password=password)
            flash(
                "Sign-up successful. Please confirm your email, then log in.",
                "success",
            )
            return redirect(url_for("main.login"))
        except Exception as exc:
            flash(f"Sign-up failed: {exc}", "error")

    return render_template("signup.html")


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        supabase_service = get_supabase_service()

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("login.html")

        try:
            auth_response = supabase_service.sign_in(email=email, password=password)
            session["user_email"] = auth_response.user.email
            return redirect(url_for("main.chat"))
        except Exception as exc:
            flash(
                f"Login failed. Confirm your email first if needed. Details: {exc}",
                "error",
            )

    return render_template("login.html")


@main_bp.route("/chat")
def chat():
    user_email = session.get("user_email")
    if not user_email:
        return redirect(url_for("main.login"))
    return render_template("chat.html", user_email=user_email)


@main_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))
