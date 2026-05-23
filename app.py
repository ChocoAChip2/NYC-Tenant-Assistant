from flask import Flask

from config import load_settings
from routes import main_bp
from supabase_service import SupabaseService


def create_app() -> Flask:
    settings = load_settings()
    app = Flask(__name__)
    app.secret_key = settings.flask_secret_key

    supabase_service = SupabaseService.from_settings(settings)
    app.config["SUPABASE_SERVICE"] = supabase_service

    if not supabase_service.is_ready():
        print(f"❌ {supabase_service.initialization_error}", flush=True)
    else:
        print("✅ Supabase client ready.", flush=True)

    app.register_blueprint(main_bp)
    return app


app = create_app()


if __name__ == "__main__":
    app_settings = load_settings()
    app.run(host="0.0.0.0", port=app_settings.port)
