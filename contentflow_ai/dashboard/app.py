from __future__ import annotations

from flask import Flask

from . import services


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

    @app.context_processor
    def inject_branding():
        branding = services.load_branding()
        return {"branding": branding, "logo": services.logo_display(branding)}

    from .routes import bp

    app.register_blueprint(bp)
    return app


def main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
