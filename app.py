from pathlib import Path
from urllib.parse import urlencode
import os
import secrets
import requests
from cryptography.fernet import Fernet
from flask import Flask, abort, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

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

with app.app_context():
    db.create_all()

@app.route("/", methods=["GET", "POST"])
def landing():
    return render_template("landing.html")

@app.route("/auth/github/callback")
def github_callback():
    if request.args.get("error"):
        return "Connexion GitHub refusée.", 400

    if not secrets.compare_digest(
        request.args.get("state", ""),
        session.get("oauth_state", "")
    ):
        abort(400)

    code = request.args.get("code")
    if not code:
        abort(400)

    token_response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": os.environ["GITHUB_CLIENT_ID"],
            "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
            "code": code,
            "redirect_uri": url_for("github_callback", _external=True),
        },
        timeout=10,
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]
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

    db.session.commit()
    session["github_user"] = github_user["login"]
    session["github_account_id"] = account.id
    session.pop("oauth_state", None)
    return redirect("/repository")

@app.route("/github/oauth", methods=["GET"])
def github_oauth():
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    params = {
        "client_id": os.environ["GITHUB_CLIENT_ID"],
        "redirect_uri": url_for("github_callback", _external=True),
        "scope": "read:user repo",
        "state": state,
    }

    github_url = "https://github.com/login/oauth/authorize?" + urlencode(params)

    return redirect(github_url)

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
        for repository in response.json()
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=90, debug=True)
