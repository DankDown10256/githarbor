# GitHarbor

GitHarbor is a self-hosted application for connecting a GitHub account, selecting repositories, and keeping backup information on your own server.

## Prerequisites

- A GitHub account
- A GitHub OAuth App created for the GitHarbor instance

## Installation

Run the installation script:

```bash
./scripts/installation.sh
```

The script creates a local `.env` file, ignored by Git, and asks for the OAuth App Client ID when needed.

Then start the Flask web server

```bash
python3 app.py
```

The web server will start at port 90.

It stores the following variables:

```env
FLASK_SECRET_KEY=...
TOKEN_ENCRYPTION_KEY=...
GITHUB_CLIENT_ID=...
```

`FLASK_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` are generated automatically. GitHub provides the Client ID.

Never commit the `.env` file or share `TOKEN_ENCRYPTION_KEY`.

## Create a GitHub OAuth App

1. In GitHub, open **Settings → Developer settings → OAuth Apps**.
2. Click **New OAuth App**.
3. Enable **Device Flow** in the OAuth App settings, then save the application.
4. Copy the **Client ID**.
5. Run `./scripts/installation.sh` and enter the Client ID when prompted.

GitHarbor uses the Device Flow, so it does not use an authorization callback URL or a Client Secret. Each administrator hosting their own instance must create their own GitHub OAuth App.

## Start the application

```bash
python app.py
```

Then open [http://127.0.0.1:90](http://127.0.0.1:90).

## Local data

On first start, GitHarbor creates the following SQLite database:

```text
data/githarbor.db
```

It is local to the instance and ignored by Git. OAuth access tokens are encrypted with `TOKEN_ENCRYPTION_KEY` before being stored.

The application currently lets you:

- connect a GitHub account with the GitHub Device Flow;
- retrieve repositories accessible by that account;
- store selected repositories in the local database.

Scheduled Git mirroring every 24 hours is not implemented yet.

## License

This project is distributed under the [MIT License](LICENSE).
