import hashlib
import hmac
import os
import secrets

from flask import Flask, jsonify, request


def create_app() -> Flask:
    app = Flask(__name__)

    # SECRET_KEY: in a real deploy the SealedSecret always provides this.
    # Locally / in tests we fall back to a per-process random key so the app
    # still boots; `/version` exposes which source produced the current key
    # so an operator can tell at a glance whether the secret pipeline wired
    # up correctly.
    secret_key_env = os.environ.get("SECRET_KEY")
    if secret_key_env:
        app.secret_key = secret_key_env
        secret_source = "env"
    else:
        app.secret_key = secrets.token_urlsafe(32)
        secret_source = "generated"

    secret_fingerprint = hashlib.sha256(
        app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    ).hexdigest()[:8]

    @app.get("/")
    def index():
        return jsonify(message="cicd-python-webapp")

    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/version")
    def version():
        return jsonify(
            version=os.environ.get("APP_VERSION", "dev"),
            secret_fingerprint=secret_fingerprint,
            secret_source=secret_source,
        )

    @app.get("/admin")
    def admin():
        # Read ADMIN_TOKEN per-request so a SealedSecret rotation (which
        # triggers a deployment restart anyway) takes effect without an
        # app restart in tests.
        expected = os.environ.get("ADMIN_TOKEN")
        if not expected:
            return jsonify(error="admin endpoint not configured"), 503

        auth = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            return jsonify(error="missing or malformed Authorization header"), 401

        provided = auth[len(prefix) :]
        # Constant-time comparison — naive `==` leaks token length and
        # prefix-match info through timing side channels.
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            return jsonify(error="invalid token"), 401

        return jsonify(status="authenticated", message="hello, admin")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
