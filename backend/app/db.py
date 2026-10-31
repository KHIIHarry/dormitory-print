from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import Request

engine = None
SessionLocal = sessionmaker(expire_on_commit=False, autoflush=False)


def configure(url=None):
    global engine
    from .settings import get_settings
    url = url or get_settings().database_url
    if engine is not None:
        engine.dispose()
    engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    SessionLocal.configure(bind=engine)
    return engine


def get_db(request: Request):
    # Transaction ownership is the HTTP middleware, not individual endpoints.
    yield request.state.db
