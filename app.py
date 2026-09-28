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
from sqlalchemy import inspect, text

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
database_path = Path(__file__).resolve().parent / "data" / "githarbor.db"
database_path.parent.mkdir(exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
token_cipher = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())

GITHUB_API = "https://api.github.com"
REPOS_PER_PAGE = 100
MAX_REPO_PAGES = 50
GIT_TOKEN_ENV = "GITHARBOR_GIT_TOKEN"
# Passed to git with `-c`: credentials come from the environment so the token
# never shows up in the process list nor in the mirror's git config.
GIT_CREDENTIAL_HELPER = (
    rf"""!f() {{ printf 'username=%s\n' 'x-access-token'; """
    rf"""printf 'password=%s\n' "${GIT_TOKEN_ENV}"; }}; f"""
)


class GitHubAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    github_id = db.Column(db.Integer, unique=True, nullable=False)
    login = db.Column(db.String(255), nullable=False)
    encrypted_access_token = db.Column(db.Text, nullable=False)
    # Scopes granted by the user during the Device Flow, e.g. "repo,read:user".
    scope = db.Column(db.Text, nullable=True, default="")

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
    private = db.Column(db.Boolean, nullable=False, default=False)
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


def ensure_schema():
    """Add columns introduced after the tables were first created."""

    added_columns = {
        "git_hub_account": {"scope": "scope TEXT"},
        "mirrored_repository": {"private": "private BOOLEAN NOT NULL DEFAULT 0"},
    }

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in added_columns.items():
        if table not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing_columns:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {definition}"))

    db.session.commit()


with app.app_context():
    db.create_all()
    ensure_schema()


def account_token(account):
    """Decrypt the OAuth access token stored for an account."""

    return token_cipher.decrypt(account.encrypted_access_token.encode()).decode()


def github_headers(access_token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
    }


def account_has_private_scope(account):
    """True when the granted scopes allow reading private repositories."""

    granted = (account.scope or "").split(",")
    return any(scope.strip() == "repo" for scope in granted)


def fetch_user_repos(access_token):
    """List every repository the token can see, following pagination."""

    repositories = []

    for page in range(1, MAX_REPO_PAGES + 1):
        response = requests.get(
            f"{GITHUB_API}/user/repos",
            headers=github_headers(access_token),
            params={
                "affiliation": "owner,collaborator,organization_member",
                "per_page": REPOS_PER_PAGE,
                "page": page,
            },
            timeout=10,
        )
        response.raise_for_status()
        batch = response.json()
        repositories.extend(batch)

        if len(batch) < REPOS_PER_PAGE:
            break

    return repositories

@app.route("/", methods=["GET"])
def landing():
    # If user is logged in, redirect to accounts page
    if "github_account_id" in session:
        return redirect(url_for("accounts"))
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
    granted_scope = token_data.get("scope", "")
    user_response = requests.get(
        "https://api.github.com/user",
        headers=github_headers(access_token),
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
            scope=granted_scope,
        )
        db.session.add(account)
    else:
        account.login = github_user["login"]
        account.encrypted_access_token = encrypted_access_token
        account.scope = granted_scope

    db.session.delete(authorization)
    db.session.commit()
    session["github_user"] = github_user["login"]
    session["github_account_id"] = account.id
    session.pop("device_authorization_id", None)

    # Redirect to the stored next path, or repository as default
    next_url = session.pop("next_after_auth", None)
    if next_url:
        return jsonify(status="authorized", redirect_url=next_url)
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

    access_token = account_token(account)
    try:
        repos_data = fetch_user_repos(access_token)
    except requests.HTTPError as error:
        # The token is no longer valid: start a fresh Device Flow.
        if error.response is not None and error.response.status_code == 401:
            session.clear()
            return redirect("/github/oauth")
        raise

    repositories = [
        {
            "id": repository["id"],
            "name": repository["full_name"],
            "description": repository.get("description"),
            "visibility": repository.get(
                "visibility", "private" if repository["private"] else "public"
            ),
        }
        for repository in repos_data
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
                        private = repo.get("private", False),
                        enabled = True,
                    )
                    db.session.add(saved_repo)
                else:
                    saved_repo.full_name = repo["full_name"]
                    saved_repo.clone_url = repo["clone_url"]
                    saved_repo.private = repo.get("private", False)
                    saved_repo.enabled = True

            elif saved_repo is not None:
                saved_repo.enabled = False

        db.session.commit()
        return redirect(url_for("repository"))

    return render_template(
        "repository.html",
        repositories=repositories,
        protected_repository_ids = protected_repository_ids,
        private_scope_ok = account_has_private_scope(account),
    )

@app.route("/protect", methods=["GET"])
def protect():
    # Store the intended redirect path for after auth (if not already set)
    if "next_after_auth" not in session:
        session["next_after_auth"] = url_for("accounts")
    if "github_account_id" in session:
        return redirect(url_for("accounts"))
    return redirect(url_for("github_oauth"))


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/accounts", methods=["GET"])
def accounts():
    if "github_account_id" not in session:
        return redirect(url_for("protect"))
    
    all_accounts = GitHubAccount.query.all()
    current_account_id = session.get("github_account_id")
    
    accounts_list = []
    for account in all_accounts:
        protected_count = MirroredRepository.query.filter_by(
            github_account_id=account.id,
            enabled=True
        ).count()
        
        accounts_list.append({
            "id": account.id,
            "login": account.login,
            "github_id": account.github_id,
            "is_current": account.id == current_account_id,
            "protected_repos_count": protected_count,
        })
    
    if current_account_id is None and accounts_list:
        session["github_account_id"] = accounts_list[0]["id"]
        session["github_user"] = accounts_list[0]["login"]
        return redirect(url_for("accounts"))
    
    return render_template("accounts.html", accounts=accounts_list)


@app.route("/accounts/switch/<int:account_id>", methods=["GET"])
def switch_account(account_id):
    account = db.session.get(GitHubAccount, account_id)
    if account is None:
        return redirect(url_for("accounts"))
    
    session["github_account_id"] = account.id
    session["github_user"] = account.login
    
    return redirect(url_for("repository"))


@app.route("/accounts/connect", methods=["GET"])
def connect_new_account():
    session["next_after_auth"] = url_for("accounts")
    return redirect(url_for("github_oauth"))


@app.cli.command("run_backups")
def run_backups():
    repositories = MirroredRepository.query.filter_by(enabled=True).all()
    failures = []

    for repository in repositories:
        click.echo(f"Backing up {repository.full_name}")

        account = db.session.get(GitHubAccount, repository.github_account_id)
        if account is None:
            click.echo("  Skipped: the linked GitHub account no longer exists.")
            failures.append(repository.full_name)
            continue

        if repository.private and not account_has_private_scope(account):
            click.echo(
                "  Skipped: private repository, but the account token was not "
                f"granted the 'repo' scope (granted: {account.scope or 'none'})."
            )
            failures.append(repository.full_name)
            continue

        mirror_path = (
            Path("data/mirrors")
            / str(repository.github_account_id)
            / f"{repository.github_repository_id}.git"
        )
        try:
            if mirror_path.is_dir():
                print("Repo already cloned updating...")
                run_git(["-C", str(mirror_path), "remote", "update", "--prune"], account)
            else:
                print("Repo doesn't found cloning it...")
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                run_git(
                    ["clone", "--mirror", repository.clone_url, str(mirror_path)],
                    account,
                )
        except subprocess.CalledProcessError as error:
            click.echo(f"  Backup failed (git exit code {error.returncode}).")
            failures.append(repository.full_name)

    if failures:
        raise click.ClickException(
            f"{len(failures)} backup(s) failed: {', '.join(failures)}"
        )


def run_git(arguments, account):
    """Run a git command with the account's OAuth token as credentials."""

    environment = os.environ.copy()
    environment[GIT_TOKEN_ENV] = account_token(account)
    environment["GIT_TERMINAL_PROMPT"] = "0"

    subprocess.run(
        [
            "git",
            # Reset the helper list so only ours is used: no credential is
            # written to a keyring and no stale credential overrides the token.
            "-c",
            "credential.helper=",
            "-c",
            f"credential.helper={GIT_CREDENTIAL_HELPER}",
            *arguments,
        ],
        check=True,
        env=environment,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1024)
