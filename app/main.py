import os

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return jsonify(message="cicd-python-webapp")

    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/version")
    def version():
        return jsonify(version=os.environ.get("APP_VERSION", "dev"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
