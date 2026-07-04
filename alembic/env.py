"""Alembic environment configuration for GrACE."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from src.shared.config import get_settings
from src.shared.database import Base

# Import ORM models so Alembic detects them for autogenerate
from src.discovery.database import ProcessedDocumentRow  # noqa: F401
from src.discovery.cq_database import CompetencyQuestionRow, CQClusterRow, MergeRunRow  # noqa: F401
from src.ontology.database import (  # noqa: F401
    OntologyVersionRow, SchemaProposalRow, CalibrationRecordRow, SchemaPromotionEventRow,
)
from src.graph.schema_sync_database import GraphSchemaSyncRow  # noqa: F401
from src.graph.namespace_database import GraphNamespaceRow  # noqa: F401

# Alembic Config object
config = context.config

# Set the database URL from GraceSettings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up Python logging from the .ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# MetaData for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
