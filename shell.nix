{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    (pkgs.python3.withPackages (ps: with ps; [
      flask
      flask_sqlalchemy
      cryptography
      python-dotenv
      requests
    ]))
    pkgs.docker
    pkgs.docker-compose
  ];

  shellHook = ''
    echo "Flask development environment ready"
  '';
}
