"""What the reaper drops, and what it must leave alone. The preservation cases
carry the weight: three shapes that match and must survive a FORCE drop."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy.engine import make_url

from codify import testing

pytestmark = pytest.mark.integration

# Per run throughout: two suites at once would otherwise seed the same names
# and contend on the same lock keys, so one would report the other's outcome.
_RUN = testing._run_id()[:6]
_TEMPLATE = f"wigreap{_RUN}"
_DEAD = f"dead{_RUN}"
_LIVE = f"live{_RUN}"
_NAMES = {
    "dead": f"{_TEMPLATE}_{_DEAD}_gw0_lk",
    "live": f"{_TEMPLATE}_{_LIVE}_gw0_lk",
    "legacy": f"{_TEMPLATE}_gw0",
    # An older checkout makes this shape and claims no lock.
    "unmarked": f"{_TEMPLATE}_{_DEAD}_gw1",
    # A template ending in the marker must not let the old grammar through.
    "lktemplate": f"{_TEMPLATE}_lk_{_DEAD}_gw2",
    "other": f"{_TEMPLATE}x_somerun_gw0",
}


def _admin(url):  # type: ignore[no-untyped-def]
    dsn = url.set(drivername="postgresql", database="postgres")
    return psycopg.connect(dsn.render_as_string(hide_password=False), connect_timeout=5)


@pytest.fixture
def seeded() -> Iterator[object]:
    raw = os.environ.get("POSTGRES_URL")
    if not raw:
        pytest.skip("POSTGRES_URL unset")
    url = make_url(raw)
    with _admin(url) as c:
        c.autocommit = True
        for n in _NAMES.values():
            c.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')  # noqa: S608
            c.execute(f'CREATE DATABASE "{n}"')  # noqa: S608
    yield url
    with _admin(url) as c:
        c.autocommit = True
        for n in _NAMES.values():
            c.execute(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')  # noqa: S608


def test_a_dead_run_goes_and_everything_else_stays(seeded) -> None:  # type: ignore[no-untyped-def]
    url = seeded
    # A live run holds its key shared for as long as its connection is open.
    holder = _admin(url)
    holder.execute("SELECT pg_advisory_lock_shared(%s)", (testing._run_lock_key(_LIVE),))
    holder.commit()
    try:
        dropped = testing.reap_dead_run_databases(url, keep_run="mine", template=_TEMPLATE)
    finally:
        holder.close()

    assert dropped == [_NAMES["dead"]], dropped
    with _admin(url) as c:
        present = {
            n
            for n in _NAMES.values()
            if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (n,)).fetchone()
        }
    assert present == {
        _NAMES["live"],
        _NAMES["legacy"],
        _NAMES["other"],
        _NAMES["unmarked"],
        _NAMES["lktemplate"],
    }
