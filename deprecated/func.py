

import base64
import gzip
import io
import json
import logging
import re
import time
import traceback
import uuid
from functools import wraps
from datetime import datetime

import oci
import pydantic
import pydantic_settings
from sqlalchemy import create_engine, text, exc, Table, MetaData, Column, values, column
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import JSONB, TEXT, UUID as PG_UUID
from sqlalchemy.sql import delete, insert
from pgvector.sqlalchemy import Vector

# --- 0. Application-Specific Constants ---
VECTOR_DIMENSION = 1536

# --- 1. Advanced Structured Logging ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "invocation_id": getattr(record, 'invocation_id', 'N/A'),
            "logger_name": record.name,
        }
        extra_fields = {k: v for k, v in record.__dict__.items() if k not in logging.LogRecord('', 0, '', 0, '', (), None, None).__dict__}
        if extra_fields:
            log_record.update(extra_fields)
        if record.exc_info:
            log_record['exception'] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": "".join(traceback.format_exception(*record.exc_info))
            }
        return json.dumps(log_record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False

# --- 2. Context-Aware Logging Decorator ---
def with_invocation_context(func):
    @wraps(func)
    def wrapper(ctx, data: io.BytesIO = None):
        headers = ctx.Headers()
        invocation_id = headers.get("fn-invoke-id") or str(uuid.uuid4())
        ctx.log = logging.LoggerAdapter(logger, {'invocation_id': invocation_id})
        ctx.log.info("Function invocation started.")
        try:
            return func(ctx, data)
        except Exception as e:
            ctx.log.critical(f"An unhandled exception reached the top-level wrapper: {e}", exc_info=True)
            raise
    return wrapper
            
# --- 3. Strict Configuration Validation with Pydantic ---
class ConfigurationError(Exception): pass
class DbSecret(pydantic.BaseModel):
    username: str
    password: pydantic.SecretStr
    host: str
    port: int = 5432
    dbname: str
class Settings(pydantic_settings.BaseSettings):
    DB_SECRET_OCID: str
    OCI_NAMESPACE: str
    model_config = pydantic_settings.SettingsConfigDict(extra='ignore')

# --- 4. Core Logic ---
db_engine: Engine | None = None

# ... (_get_db_engine, _download_and_parse_payload - NO CHANGES) ...
def _get_db_engine(settings: Settings, log: logging.LoggerAdapter) -> Engine:
    global db_engine
    if db_engine is not None:
        try:
            with db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            log.info("Reusing existing, healthy database engine.")
            return db_engine
        except exc.OperationalError as e:
            log.warning(f"Stale connection detected. Recreating engine. Error: {e}")
            db_engine = None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            log.info(f"Initializing database engine (attempt {attempt + 1}/{max_retries}).")
            signer = oci.auth.signers.get_resource_principals_signer()
            secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
            
            log.info("Fetching secret from Vault.", extra={"secret_ocid": settings.DB_SECRET_OCID})
            secret_bundle = secrets_client.get_secret_bundle(secret_id=settings.DB_SECRET_OCID, stage="LATEST")
           
            secret_content_base64 = secret_bundle.data.secret_bundle_content.content
            decoded_bytes = base64.b64decode(secret_content_base64)
            secret_content = decoded_bytes.decode('utf-8')
                
            db_secret_data = json.loads(secret_content)
            db_config = DbSecret.model_validate(db_secret_data)
            
            db_connection_string = (
                f"postgresql+psycopg://{db_config.username}:{db_config.password.get_secret_value()}"
                f"@{db_config.host}:{db_config.port}/{db_config.dbname}"
            )
            
            db_engine = create_engine(
                db_connection_string,
                pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800,
                connect_args={"connect_timeout": 10, "application_name": "rag-ingestion-fn"}
            )

            with db_engine.connect() as connection:
                db_version = connection.execute(text("SELECT version()")).scalar()
            log.info("Database engine initialized and connection validated.", extra={"db_version": db_version})
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

def _download_and_parse_payload(settings: Settings, bucket_name: str, object_name: str, log: logging.LoggerAdapter) -> dict:
    log.info(f"Downloading object '{object_name}' from bucket '{bucket_name}'.")
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        get_obj = object_storage_client.get_object(settings.OCI_NAMESPACE, bucket_name, object_name)
        with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
            payload = json.load(gz_file)
        log.info("Successfully downloaded and parsed payload.")
        return payload
    except Exception as e:
        log.error(f"Failed to download or parse payload for object '{object_name}'.", exc_info=True)
        raise

# NEW: Helper function for secure, dynamic table validation
def _validate_table_exists(connection: 'Connection', table_name: str, log: logging.LoggerAdapter):
    """
    Securely checks if a table exists in the database.
    This prevents SQL injection and ensures the ingestor only acts on provisioned tables.
    """
    # 1. First, perform a strict regex check on the table name format.
    # This pattern matches the one defined in the indexer.
    if not re.match(r'^codebase_collection_[a-zA-Z0-9_]+$', table_name):
        log.error("Table name failed syntactic validation.", table_name=table_name)
        raise ValueError(f"Payload provides a syntactically invalid table name: {table_name}")

    # 2. Second, query the database's information schema using a parameterized query.
    # This is a safe way to check for table existence without risk of injection.
    query = text("""
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = :table_name
    """)
    result = connection.execute(query, {"table_name": table_name}).scalar_one_or_none()
    
    if result is None:
        log.error("Validation failed: Table does not exist in the database.", table_name=table_name)
        raise ValueError(f"Attempted to ingest data for non-existent table: {table_name}. The CI/CD pipeline must create this table first.")
    
    log.info("Table validation successful. Table exists.", table_name=table_name)


def _process_database_transaction(engine: Engine, payload: dict, log: logging.LoggerAdapter):
    table_name_raw = payload.get("table_name")
    chunks_to_upsert = payload.get("chunks_to_upsert", [])
    files_to_delete = payload.get("files_to_delete", [])

    if not table_name_raw:
        raise ValueError("Payload is missing required 'table_name' field.")
    
    # The raw name is now the final name after validation
    table_name = table_name_raw

    metadata_obj = MetaData()
    target_table = Table(
        table_name,
        metadata_obj,
        Column("id", PG_UUID, primary_key=True),
        Column("content", TEXT),
        Column("metadata", JSONB),
        Column("embedding", Vector(VECTOR_DIMENSION))
    )

    log.info(f"Beginning database transaction for table '{table_name}'.", extra={
        "chunks_to_upsert": len(chunks_to_upsert),
        "files_to_delete": len(files_to_delete)
    })

    with engine.connect() as connection:
        # MODIFIED: Perform dynamic validation before starting the transaction
        _validate_table_exists(connection, table_name, log)

        with connection.begin() as transaction:
            try:
                # ... (The rest of the transaction logic using VALUES clause remains the same) ...
                if files_to_delete:
                    log.info(f"Deleting {len(files_to_delete)} source files.")
                    vals = values(column("source_file", TEXT), name="files_to_delete_values").data(
                        [(f,) for f in files_to_delete]
                    )
                    delete_stmt = delete(target_table).where(
                        target_table.c.metadata['source'].astext == vals.c.source_file
                    )
                    connection.execute(delete_stmt)

                if chunks_to_upsert:
                    source_files_to_update = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    log.info(f"Upserting data for {len(source_files_to_update)} source files.")
                    
                    if source_files_to_update:
                        update_vals = values(column("source_file", TEXT), name="files_to_update_values").data(
                            [(f,) for f in source_files_to_update]
                        )
                        upsert_delete_stmt = delete(target_table).where(
                            target_table.c.metadata['source'].astext == update_vals.c.source_file
                        )
                        connection.execute(upsert_delete_stmt)
                    
                    records_to_insert = [
                        {
                            "id": chunk.get("id"), 
                            "content": chunk.get("document"), 
                            "metadata": chunk.get("metadata"),
                            "embedding": chunk.get("embedding")
                        }
                        for chunk in chunks_to_upsert if chunk.get("document")
                    ]
                    
                    if records_to_insert:
                        insert_stmt = insert(target_table)
                        batch_size = 500
                        log.info(f"Inserting {len(records_to_insert)} new chunks in batches of {batch_size}.")
                        for i in range(0, len(records_to_insert), batch_size):
                            batch = records_to_insert[i:i + batch_size]
                            connection.execute(insert_stmt, batch)

                transaction.commit()
                log.info("Transaction committed successfully.")
            except exc.SQLAlchemyError as e:
                log.error("Database transaction failed. Rolling back.", exc_info=True)
                transaction.rollback()
                raise

@with_invocation_context
def handler(ctx, data: io.BytesIO = None):
    log = ctx.log
    try:
        log.info("Validating configuration.")
        settings = Settings()
        
        body = data.getvalue()
        if not body:
            raise ValueError("Received empty event payload.")
        event = json.loads(body.decode('utf-8'))
        
        data = event.get('data', {})
        bucket_name = data.get('additionalDetails', {}).get('bucketName')
        object_name = data.get('resourceName')
        if not bucket_name or not object_name:
            raise ValueError("Event data is missing bucketName or resourceName.")
        log.info("Event parsed successfully.", extra={"bucket": bucket_name, "object": object_name})

        payload = _download_and_parse_payload(settings, bucket_name, object_name, log)
        engine = _get_db_engine(settings, log)
        _process_database_transaction(engine, payload, log)
        
        invocation_id = log.extra['invocation_id']
        log.info("Function invocation completed successfully.")
        return {"status": "success", "message": f"Processed {object_name} successfully.", "invocation_id": invocation_id}

    except pydantic.ValidationError as e:
        log.error(f"Configuration validation failed: {e}", exc_info=True)
        raise ConfigurationError(f"Invalid configuration: {e}") from e
    except Exception as e:
        log.error(f"Error during function execution: {e}", exc_info=True)
        raise