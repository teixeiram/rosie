"""Account admin from the command line (there is no sign-up page).

    docker compose exec rosie python -m rosie.manage add-user alex "Alex" admin
    docker compose exec rosie python -m rosie.manage add-user <name> "<Name>" member
    docker compose exec rosie python -m rosie.manage set-password <name>
    docker compose exec rosie python -m rosie.manage list-users

Passwords are read from the terminal without echo (or from stdin when piped).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import auth, db
from .config import Settings


def _read_password() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    p1 = getpass.getpass("New password (10+ characters): ")
    p2 = getpass.getpass("Again: ")
    if p1 != p2:
        sys.exit("Passwords don't match.")
    return p1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rosie.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user")
    a.add_argument("username")
    a.add_argument("display_name")
    a.add_argument("role", choices=auth.ROLES)
    s = sub.add_parser("set-password")
    s.add_argument("username")
    sub.add_parser("list-users")
    args = ap.parse_args(argv)

    conn = db.connect(Settings.from_env().db_path)
    try:
        if args.cmd == "add-user":
            u = auth.create_user(
                conn, args.username, args.display_name, args.role, _read_password()
            )
            print(f"Added {u.username} ({u.role}).")
        elif args.cmd == "set-password":
            auth.set_password(conn, args.username, _read_password())
            print(f"Password changed for {args.username}; their devices are signed out.")
        elif args.cmd == "list-users":
            for r in conn.execute("SELECT username, display_name, role FROM users ORDER BY id"):
                print(f"{r['username']:<16} {r['role']:<7} {r['display_name']}")
    except ValueError as e:
        sys.exit(str(e))
    except Exception as e:  # sqlite IntegrityError etc.
        sys.exit(f"Failed: {e}")


if __name__ == "__main__":
    main()
