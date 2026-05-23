import os

from flask import Flask, flash, redirect, render_template, request, session, url_for
from supabase import Client, create_client

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-render")

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

supabase: Client | None = None
if not url or not key:
    print("❌ Supabase keys are missing.", flush=True)
else:
    try:
        supabase = create_client(url, key)
        print("✅ Supabase client ready.", flush=True)
    except Exception as e:
        print(f"❌ Failed to create Supabase client: {e}", flush=True)


@app.route("/", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if not supabase:
            flash("Supabase is not configured yet.", "error")
            return render_template("signup.html")

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("signup.html")

        try:
            supabase.auth.sign_up({"email": email, "password": password})
            flash(
                "Sign-up successful. Please confirm your email, then log in.",
                "success",
            )
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Sign-up failed: {e}", "error")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not supabase:
            flash("Supabase is not configured yet.", "error")
            return render_template("login.html")

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("login.html")

        try:
            auth_response = supabase.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            session["user_email"] = auth_response.user.email
            return redirect(url_for("chat"))
        except Exception as e:
            flash(
                f"Login failed. Confirm your email first if needed. Details: {e}",
                "error",
            )

    return render_template("login.html")


@app.route("/chat")
def chat():
    user_email = session.get("user_email")
    if not user_email:
        return redirect(url_for("login"))
    return render_template("chat.html", user_email=user_email)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
