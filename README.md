# GitHarbor

GitHarbor is a self-hosted application for connecting a GitHub account, selecting repositories, and keeping backup information on your own server.

## Prérequis

- [Nix](https://nixos.org/download/)
- A GitHub account
- A GitHub OAuth App created for the GitHarbor instance

## Installation

Open the development environment:

```bash
nix-shell
```

Then run the installation script:

```bash
./scripts/installation.sh
```

The script creates a local `.env` file, ignored by Git, and asks for the OAuth App credentials when needed.

It stores the following variables:

```env
FLASK_SECRET_KEY=...
TOKEN_ENCRYPTION_KEY=...
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
```

`FLASK_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` are generated automatically. GitHub provides the Client ID and Client Secret.

Never commit the `.env` file or share `GITHUB_CLIENT_SECRET` or `TOKEN_ENCRYPTION_KEY`.

## Create a GitHub OAuth App

1. In GitHub, open **Settings → Developer settings → OAuth Apps**.
2. Click **New OAuth App**.
3. For a local installation running on port `90`, use:

   ```text
   Homepage URL: http://127.0.0.1:90
   Authorization callback URL: http://127.0.0.1:90/auth/github/callback
   ```

4. After creating the app, copy the **Client ID**.
5. Generate a **Client Secret** and copy it immediately: GitHub only displays it once.
6. Run `./scripts/installation.sh` and enter both values when prompted.

For a public instance, replace `http://127.0.0.1:90` with that instance's HTTPS URL in both fields.

Each administrator hosting their own instance must create their own GitHub OAuth App. Do not distribute a shared Client Secret.

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

- connect a GitHub account with OAuth;
- retrieve repositories accessible by that account;
- store selected repositories in the local database.

Scheduled Git mirroring every 24 hours is not implemented yet.

## License

This project is distributed under the [MIT License](LICENSE).
