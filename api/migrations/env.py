# migrations/env.py
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Import your models and Base here
from models import Base
import models 

config = context.config
fileConfig(config.config_file_name)
target_metadata = Base.metadata # This is the key line