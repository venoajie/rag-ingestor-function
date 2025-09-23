
import io
import os
import json
import logging
import gzip
import re
import uuid
import time
import traceback
from functools import wraps
from datetime import datetime

import oci
import pydantic
import pydantic_settings
from sqlalchemy import create_engine, text, exc
from sqlalchemy.engine import Engine

# --- 1. Advanced Structured Logging ---
# Using a custom formatter for more control over exception logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "invocation_id": getattr(record, 'invocation_id', 'N/A'),
            "logger_name": record.name,
        }
        if hasattr(record, 'extra_info'):
            log_record.update(record.extra_info)
        
        if record.exc_info:
            log_record['exception'] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": "".join(traceback.format_exception(*record.exc_info))
            }
        return json.dumps(log_record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Remove any default handlers
if logger.hasHandlers():
    logger.handlers.clear()
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False

# --- 2. Context-Aware Logging Decorator ---
def with_invocation_context(func):
    """Decorator to add invocation context and top-level error handling."""
    @wraps(func)
    def wrapper(ctx, data: io.BytesIO = None):
        invocation_id = ctx.FnInvokeID() or str(uuid.uuid4())
        
        # Use a LoggerAdapter to automatically inject the invocation_id
        adapter = logging.LoggerAdapter(logger, {'invocation_id': invocation_id})
        
        adapter.info("Function invocation started.")
        try:
            # Pass the adapter and invocation_id to the main logic
            return func(ctx, data, adapter, invocation_id)
        except Exception as e:
            adapter.critical(
                f"An unhandled exception reached the top-level wrapper: {e}",
                exc_info=True
            )
            # Re-raise to ensure the function fails correctly
            raise
    return wrapper

# --- 3. Strict Configuration Validation with Pydantic ---
class ConfigurationError(Exception):
    pass

class DbSecret(pydantic.BaseModel):
    """Schema for the JSON object stored in OCI Vault."""
    username: str
    password: pydantic.SecretStr
    host: str
    port: int = 5432
    dbname: str

class Settings(pydantic_settings.BaseSettings):
    """Application settings, validated on instantiation."""
    DB_SECRET_OCID: str
    OCI_NAMESPACE: str
    
    model_config = pydantic_settings.SettingsConfigDict(extra='ignore')

# --- 4. Refactored Core Logic ---
db_engine: Engine | None = None

def _get_db_engine(settings: Settings, log: logging.LoggerAdapter) -> Engine:
    """Initializes a resilient, cached SQLAlchemy engine with retries and timeouts."""
    global db_engine
    
    # Stale connection check
    if db_engine is not None:
        try:
            with db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            log.info("Reusing existing, healthy database engine.")
            return db_engine
        except exc.OperationalError as e:
            log.warning(f"Stale connection detected. Recreating engine. Error: {e}")
            db_engine = None

    # Connection logic with retries
    max_retries = 3
    for attempt in range(max_retries):
        try:
            log.info(f"Initializing database engine (attempt {attempt + 1}/{max_retries}).")
            signer = oci.auth.signers.get_resource_principals_signer()
            secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
            
            log.info("Fetching secret from Vault.", extra_info={"secret_ocid": settings.DB_SECRET_OCID})
            secret_bundle = secrets_client.get_secret_bundle(secret_id=settings.DB_SECRET_OCID)
            secret_content = secret_bundle.data.secret_bundle_content.content
            
            db_secret_data = json.loads(secret_content)
            db_config = DbSecret.model_validate(db_secret_data)
            
            db_connection_string = (
                f"postgresql+psycopg2://{db_config.username}:{db_config.password.get_secret_value()}"
                f"@{db_config.host}:{db_config.port}/{db_config.dbname}"
            )
            
            db_engine = create_engine(
                db_connection_string,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
                # CRITICAL: Add a connection timeout to prevent hangs
                connect_args={"connect_timeout": 10, "application_name": "rag-ingestion-fn"}
            )

            # Test connection immediately to fail fast
            with db_engine.connect() as connection:
                db_version = connection.execute(text("SELECT version()")).scalar()
            log.info("Database engine initialized and connection validated.", extra_info={"db_version": db_version})
            return db_engine

        except Exception as e:
            log.error(f"Failed to initialize database engine on attempt {attempt + 1}: {e}", exc_info=True)
            if attempt < max_retries - 1:
                sleep_time = (2 ** attempt)
                log.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                log.critical("All attempts to initialize database engine failed.")
                raise

# ... (Your other functions like _download_and_parse_payload and _process_database_transaction remain largely the same, but should accept `log` as an argument to use the contextual logger)

@with_invocation_context
def handler(ctx, data: io.BytesIO, log: logging.LoggerAdapter, invocation_id: str):
    """OCI Function entry point."""
    try:
        # 1. Configuration Validation (Fail Fast)
        log.info("Validating configuration.")
        settings = Settings()
        
        # 2. Event Parsing
        event = json.loads(data.getvalue().decode('utf-8'))
        data = event.get('data', {})
        bucket_name = data.get('additionalDetails', {}).get('bucketName')
        object_name = data.get('resourceName')
        if not bucket_name or not object_name:
            raise ValueError("Event data is missing bucketName or resourceName.")
        log.info("Event parsed successfully.", extra_info={"bucket": bucket_name, "object": object_name})

        # 3. Business Logic
        # payload = _download_and_parse_payload(settings, bucket_name, object_name, log)
        # _validate_payload(payload, log) # Add payload validation
        engine = _get_db_engine(settings, log)
        # _process_database_transaction(engine, payload, log)
        
        log.info("Function invocation completed successfully.")
        return {"status": "success", "message": f"Processed {object_name} successfully.", "invocation_id": invocation_id}

    except pydantic.ValidationError as e:
        log.error(f"Configuration validation failed: {e}", exc_info=True)
        raise ConfigurationError(f"Invalid configuration: {e}") from e
    except Exception as e:
        log.error(f"Error during function execution: {e}", exc_info=True)
        raise
