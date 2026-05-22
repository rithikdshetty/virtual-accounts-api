from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Why sync SQLAlchemy: a prototype's bottleneck is correctness, not
# concurrency. Sync code is easier to reason about and test. FastAPI runs
# sync routes in a thread pool, so the perf hit is small. Production at
# scale would switch to async + asyncpg.
#
# pool_pre_ping handles Neon's idle connection drops; without it, the first
# query after a few minutes of inactivity fails. pool_recycle forces fresh
# connections every 30 min as belt-and-suspenders.

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency. Provides a request-scoped session that closes at the
    end of every request, regardless of success or exception.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
