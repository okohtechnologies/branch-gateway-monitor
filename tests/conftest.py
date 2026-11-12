import os
import re
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from ipmon.db import Database


def _dsn() -> str | None:
    if os.environ.get("DATABASE_URL"):  # CI and Docker provide it directly
        return os.environ["DATABASE_URL"]
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return None
    m = re.search(r"^DATABASE_URL=(.+)$", env.read_text(encoding="utf-8-sig"), re.M)
    return m.group(1).strip() if m else None


@pytest_asyncio.fixture
async def db():
    """A Database in a throwaway schema, dropped afterwards."""
    dsn = _dsn()
    if not dsn:
        if os.environ.get("CI"):
            # Never let CI go green by silently skipping every database test.
            pytest.fail("DATABASE_URL is not set: database tests cannot run in CI")
        pytest.skip("no DATABASE_URL in .env")
    schema = "t_" + uuid.uuid4().hex[:10]
    database = await Database.connect(dsn, schema=schema)
    try:
        yield database
    finally:
        await database.close()
        conn = await asyncpg.connect(dsn)
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()
