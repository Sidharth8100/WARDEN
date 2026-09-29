"""Base declarative class and shared MetaData with a deterministic naming convention.

WHY a naming convention?
  Alembic autogenerate and PostgreSQL both need stable, predictable constraint names
  so that ALTER TABLE / DROP CONSTRAINT statements work correctly across environments.
  Without this, Postgres auto-assigns names like 'users_pkey' or unnamed CHECK constraints,
  which makes Alembic drift-checks noisy and cross-env migrations fragile.

Convention tokens used:
  ix  - index
  uq  - unique constraint
  ck  - check constraint
  fk  - foreign key
  pk  - primary key
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Standard Alembic-recommended naming convention.
# Every constraint/index in the schema will get a name derived from this template,
# making generated SQL deterministic regardless of the environment it runs in.
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared declarative base for all WARDEN ORM models."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
