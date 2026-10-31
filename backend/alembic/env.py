from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine,pool
from app.models import Base
from app.settings import get_settings

config=context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata=Base.metadata

if context.is_offline_mode():
    context.configure(url=get_settings().database_url,target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():context.run_migrations()
else:
    engine=create_engine(get_settings().database_url,poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
