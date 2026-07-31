# GitHarbor

GitHarbor is a self-hosted GitHub repository backup tool. It uses GitHub's Device Flow to connect an account, lets you select repositories to protect, and stores mirrors on the host running GitHarbor.

## Current capabilities

- GitHub authentication with OAuth Device Flow;
- encrypted storage of GitHub access tokens in a local SQLite database;
- repository selection through the web interface;
- full Git mirrors for selected public repositories;
- optional daily backups through a systemd timer.

Private repository mirroring is not implemented yet.

## Requirements

- Python 3.8 or later;
- `pip` and `venv`;
- Git installed on the host;
- a GitHub account;
- a GitHub OAuth App with Device Flow enabled;
- systemd and `sudo` only if daily scheduled backups are required.

## Installation

Clone the project and enter its directory:

```bash
git clone https://github.com/DankDown10256/githarbor
cd githarbor
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
pip install Flask Flask-SQLAlchemy cryptography python-dotenv requests
```

Run the installation script:

```bash
./scripts/installation.sh
```

The script creates a local `.env` file with restrictive permissions and asks for the GitHub OAuth App Client ID.

The file contains:

```env
FLASK_SECRET_KEY=...
TOKEN_ENCRYPTION_KEY=...
GITHUB_CLIENT_ID=...
```

`FLASK_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` are generated automatically. Do not commit, share, or replace `.env` after users have connected: changing the encryption key prevents GitHarbor from reading stored tokens.

## Create a GitHub OAuth App

1. Open GitHub **Settings → Developer settings → OAuth Apps**.
2. Select **New OAuth App**.
3. Set the homepage URL to your GitHarbor instance URL.
4. Create the application and enable **Device Flow** in its settings.
5. Copy the **Client ID**.
6. Run `./scripts/installation.sh` and enter the Client ID when prompted.

GitHarbor uses Device Flow, so it does not use an authorization callback URL or a Client Secret. This makes the setup suitable for self-hosted instances without a public domain.

### Optional: Nix shell

If you use Nix, the repository also includes a development shell:

```bash
nix-shell
```

## Run the web application

From the activated virtual environment:

```bash
python app.py
```

The application listens on port `90`. For a local installation, open [http://127.0.0.1:90](http://127.0.0.1:90).

Click **Protect your repositories**, follow the displayed GitHub Device Flow instructions, then select the repositories to protect.

## Backups

Selected repositories are stored in SQLite with an `enabled` flag. Run a backup manually from the activated virtual environment:

```bash
flask --app app run_backups
```

For each selected repository, GitHarbor:

- creates a mirror with `git clone --mirror` when no local mirror exists;
- updates an existing mirror with `git remote update --prune`.

Mirrors are stored under:

```text
data/mirrors/<github-account-id>/<github-repository-id>.git
```

A mirror is a bare Git repository. It contains the full history, branches, and tags, rather than a checked-out working directory.

To inspect the saved files in a mirror:

```bash
git --git-dir=data/mirrors/<github-account-id>/<github-repository-id>.git ls-tree -r --name-only HEAD
```

## Schedule daily backups with systemd

The included script creates and enables a system-wide service and timer. Run it from the activated virtual environment as the user who owns the GitHarbor installation:

```bash
./scripts/install-systemd-timer.sh
```

It uses `sudo` to install:

```text
/etc/systemd/system/githarbor-backup.service
/etc/systemd/system/githarbor-backup.timer
```

The timer runs daily at `03:00`. `Persistent=true` causes a missed run to execute after the server starts again.

Useful commands:

```bash
sudo systemctl start githarbor-backup.service
systemctl list-timers githarbor-backup.timer
journalctl -u githarbor-backup.service
```

The timer installation script requires systemd. On systems that use another init system, run `flask --app app run_backups` from that system's scheduler instead.

## Local data and security

GitHarbor stores its database at:

```text
data/githarbor.db
```

The database and `.env` are ignored by Git. OAuth access tokens are encrypted with `TOKEN_ENCRYPTION_KEY` before being stored in SQLite.

Keep both the database and `.env` private. Back up `.env` securely if you need to restore an existing GitHarbor installation, because it contains the encryption key needed to read stored tokens.

## License

GitHarbor is distributed under the [MIT License](LICENSE).
