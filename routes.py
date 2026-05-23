"""HTTP routes for signup, login, chat, and logout pages.

app.py registers this blueprint, and each route uses the shared SupabaseService
stored in the Flask app config to handle authentication work.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from supabase_service import SupabaseService

# The blueprint groups the page routes together so app.py can register them as
# one unit.
main_bp = Blueprint("main", __name__)


def get_supabase_service() -> SupabaseService:
    """Fetch the shared service object that app.py stored on the Flask app."""

    return current_app.config["SUPABASE_SERVICE"]


@main_bp.route("/", methods=["GET", "POST"])
def signup():
    """Show the signup page and create a new account on form submission."""

    if request.method == "POST":
        # Use the service built in app.py so request handlers do not recreate the
        # Supabase client on every form submission.
        supabase_service = get_supabase_service()

        # Pull form values from the signup.html template.
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        # Stop early if the template form was submitted without both fields.
        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("signup.html")

        try:
            # Ask Supabase to create the account, then send the user to login.
            supabase_service.sign_up(email=email, password=password)
            flash(
                "Sign-up successful. Please confirm your email, then log in.",
                "success",
            )
            return redirect(url_for("main.login"))
        except Exception as exc:
            # Show the SDK or configuration error on the same page.
            flash(f"Sign-up failed: {exc}", "error")

    return render_template("signup.html")


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    """Show the login page and create a browser session after authentication."""

    if request.method == "POST":
        supabase_service = get_supabase_service()

        # Pull form values from the login.html template.
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("login.html")

        try:
            # Save the logged-in email in the Flask session so chat() can decide
            # whether the visitor is allowed to see the protected page.
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
    """Render the placeholder chat page only for logged-in users."""

    # login() stores the email in the session; if it is missing, the visitor is
    # redirected back to the login page.
    user_email = session.get("user_email")
    if not user_email:
        return redirect(url_for("main.login"))
    return render_template("chat.html", user_email=user_email)


@main_bp.route("/logout")
def logout():
    """Clear the browser session and return the user to the login page."""

    session.clear()
    return redirect(url_for("main.login"))
