"""Codify pipeline constants (non-environment, non-configurable)."""

EMBEDDING_BATCH_SIZE = 100


def database_url() -> str:
    """`POSTGRES_URL` with the asyncpg driver.

    Unset, it is the compose database, but only where `ENVIRONMENT` says local;
    anywhere else an absent URL is an error rather than a silent default."""
    import os

    from sqlalchemy.engine import make_url

    raw = os.environ.get("POSTGRES_URL")
    if not raw:
        if os.environ.get("ENVIRONMENT") not in {None, "", "localhost", "development"}:
            raise RuntimeError(
                "POSTGRES_URL must be set when ENVIRONMENT is not localhost/development"
            )
        raw = "postgresql://codify:codify@localhost:5432/codify"
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)
