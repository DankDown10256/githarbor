from datetime import datetime, timedelta, timezone
import os
import secrets
import requests
from cryptography.fernet import Fernet
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import click
from pathlib import Path
import subprocess

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
database_path = Path(__file__).resolve().parent / "data" / "githarbor.db"
database_path.parent.mkdir(exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
token_cipher = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())


class GitHubAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    github_id = db.Column(db.Integer, unique=True, nullable=False)
    login = db.Column(db.String(255), nullable=False)
    encrypted_access_token = db.Column(db.Text, nullable=False)

class MirroredRepository(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    github_repository_id = db.Column(db.Integer, nullable=False)
    github_account_id = db.Column(
        db.Integer,
        db.ForeignKey("git_hub_account.id"),
        nullable=False,
    )
    full_name = db.Column(db.String(255), nullable=False)
    clone_url = db.Column(db.String(500), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    __table_args__ = (
        db.UniqueConstraint(
            "github_repository_id",
            "github_account_id",
            name="unique_repository_per_account",
        ),
    )


class DeviceAuthorization(db.Model):
    id = db.Column(db.String(64), primary_key=True)
    encrypted_device_code = db.Column(db.Text, nullable=False)
    user_code = db.Column(db.String(32), nullable=False)
    verification_uri = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    interval = db.Column(db.Integer, nullable=False)


with app.app_context():
    db.create_all()

@app.route("/", methods=["GET"])
def landing():
    return render_template("landing.html")

@app.route("/github/oauth", methods=["GET"])
def github_oauth():
    response = requests.post(
        "https://github.com/login/device/code",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.environ["GITHUB_CLIENT_ID"],
            "scope": "read:user repo",
        },
        timeout=10,
    )
    response.raise_for_status()
    device_data = response.json()

    authorization = DeviceAuthorization(
        id=secrets.token_urlsafe(32),
        encrypted_device_code=token_cipher.encrypt(device_data["device_code"].encode()).decode(),
        user_code=device_data["user_code"],
        verification_uri=device_data["verification_uri"],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=device_data["expires_in"]),
        interval=device_data["interval"],
    )
    db.session.add(authorization)
    db.session.commit()

    session["device_authorization_id"] = authorization.id
    return render_template(
        "device_authorization.html",
        user_code=authorization.user_code,
        verification_uri=authorization.verification_uri,
        interval=authorization.interval,
    )


@app.route("/github/oauth/status", methods=["POST"])
def github_oauth_status():
    authorization_id = session.get("device_authorization_id")
    authorization = db.session.get(DeviceAuthorization, authorization_id)

    if authorization is None:
        return jsonify(status="expired"), 400

    if datetime.now(timezone.utc) >= authorization.expires_at:
        db.session.delete(authorization)
        db.session.commit()
        session.pop("device_authorization_id", None)
        return jsonify(status="expired"), 400

    device_code = token_cipher.decrypt(authorization.encrypted_device_code.encode()).decode()
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.environ["GITHUB_CLIENT_ID"],
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=10,
    )
    token_data = response.json()

    if token_data.get("error") == "authorization_pending":
        return jsonify(status="pending", interval=authorization.interval)

    if token_data.get("error") == "slow_down":
        authorization.interval += 5
        db.session.commit()
        return jsonify(status="pending", interval=authorization.interval)

    if token_data.get("error"):
        db.session.delete(authorization)
        db.session.commit()
        session.pop("device_authorization_id", None)
        return jsonify(status="failed", error=token_data["error"]), 400

    access_token = token_data["access_token"]
    user_response = requests.get(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )
    user_response.raise_for_status()
    github_user = user_response.json()

    account = GitHubAccount.query.filter_by(github_id=github_user["id"]).first()
    encrypted_access_token = token_cipher.encrypt(access_token.encode()).decode()

    if account is None:
        account = GitHubAccount(
            github_id=github_user["id"],
            login=github_user["login"],
            encrypted_access_token=encrypted_access_token,
        )
        db.session.add(account)
    else:
        account.login = github_user["login"]
        account.encrypted_access_token = encrypted_access_token

    db.session.delete(authorization)
    db.session.commit()
    session["github_user"] = github_user["login"]
    session["github_account_id"] = account.id
    session.pop("device_authorization_id", None)

    return jsonify(status="authorized", redirect_url=url_for("repository"))

@app.route("/repository", methods=["GET", "POST"])
def repository():
    account_id = session.get("github_account_id")
    if account_id is None:
        return redirect("/github/oauth")

    account = db.session.get(GitHubAccount, account_id)
    if account is None:
        session.clear()
        return redirect("/github/oauth")

    access_token = token_cipher.decrypt(account.encrypted_access_token.encode()).decode()
    response = requests.get(
        "https://api.github.com/user/repos",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
        },
        params={
            "affiliation": "owner,collaborator,organization_member",
            "per_page": 100,
        },
        timeout=10,
    )
    response.raise_for_status()
    repos_data = response.json()

    repositories = [
        {
            "id": repository["id"],
            "name": repository["full_name"],
            "description": repository.get("description"),
            "visibility": repository.get(
                "visibility", "private" if repository["private"] else "public"
            ),
        }
    ]

    protected_repository_ids = {
        saved_repo.github_repository_id
        for saved_repo in MirroredRepository.query.filter_by(
            github_account_id = account.id,
            enabled = True,
        ).all()
    }

    if request.method == "POST":
        selected_ids = {
            int(repo_id)
            for repo_id in request.form.getlist("repositories")
        }
        for repo in repos_data:
            saved_repo = MirroredRepository.query.filter_by(
                github_repository_id = repo["id"],
                github_account_id = account.id,
            ).first()

            if repo["id"] in selected_ids:
                if saved_repo is None:
                    saved_repo = MirroredRepository(
                        github_repository_id = repo["id"],
                        github_account_id = account.id,
                        full_name = repo["full_name"],
                        clone_url = repo["clone_url"],
                        enabled = True,
                    )
                    db.session.add(saved_repo)
                else:
                    saved_repo.full_name = repo["full_name"]
                    saved_repo.clone_url = repo["clone_url"]
                    saved_repo.enabled = True

            elif saved_repo is not None:
                saved_repo.enabled = False

        db.session.commit()
        return redirect(url_for("repository"))

    return render_template("repository.html", repositories=repositories, protected_repository_ids = protected_repository_ids)

@app.route("/protect", methods=["GET"])
def protect():
    if "github_user" in session:
        return redirect("/repository")
    return redirect("/github/oauth")

@app.cli.command("run_backups")
def run_backups():
    repositories = MirroredRepository.query.filter_by(enabled=True).all()

    for repository in repositories:
        click.echo(f"Backing up {repository.full_name}")
        mirror_path = (
            Path("data/mirrors")
            / str(repository.github_account_id)
            / f"{repository.github_repository_id}.git"
        )
        if mirror_path.is_dir():
            print("Repo already cloned updating...")
            subprocess.run(
                ["git", "-C", str(mirror_path), "remote", "update", "--prune"],
                check=True,
            )
        else:
            print("Repo doesn't found cloning it...")
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--mirror", repository.clone_url, str(mirror_path)],
                check=True,
            )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1024)
