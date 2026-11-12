"""Create (or reset the password of) the dashboard admin. Prompts for the password.

Usage: .venv\\Scripts\\python scripts\\create_admin.py you@example.com
"""
import asyncio
import getpass
import sys

from ipmon.auth import hash_password
from ipmon.config import Settings
from ipmon.db import Database


async def main(email: str) -> int:
    pw = getpass.getpass("New admin password (min 10 characters): ")
    if len(pw) < 10:
        print("Password too short.")
        return 1
    if getpass.getpass("Repeat password: ") != pw:
        print("Passwords do not match.")
        return 1
    db = await Database.connect(Settings().database_url)
    try:
        await db.create_user(email, hash_password(pw))
    finally:
        await db.close()
    print(f"Admin {email.lower()} is ready. Any existing sessions for this account were signed out.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        raise SystemExit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1])))
