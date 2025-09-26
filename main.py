# main.py
import base64
import gzip
import io
import json
import logging
import re
import time
import traceback
import uuid
from datetime import datetime
from typing import Annotated

import oci
import pydantic
import pydantic_settings
from fastapi import FastAPI, Request, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text, exc, Table, MetaData, Column, values, column
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import JSONB, TEXT, UUID as PG_UUID
from sqlalchemy.sql import delete, insert
from pgvector.sqlalchemy import Vector

# --- 0. Application-Specific Constants ---
VECTOR_DIMENSION = 1536

# --- 1. Advanced Structured Logging (Unchanged) ---
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

# --- 2. Context-Aware Logging with FastAPI Dependency Injection ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})

# --- 3. Strict Configuration and Custom Exceptions ---
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

# --- 4. Core Logic Helpers ---
db_engine: Engine | None = None

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

def _validate_table_exists(connection: 'Connection', table_name: str, log: logging.LoggerAdapter):
    if not re.match(r'^codebase_collection_[a-zA-Z0-9_]+$', table_name):
        log.error("Table name failed syntactic validation.", table_name=table_name)
        raise ValueError(f"Payload provides a syntactically invalid table name: {table_name}")
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
    
    table_name = table_name_raw
    metadata_obj = MetaData()
    target_table = Table(
        table_name, metadata_obj,
        Column("id", PG_UUID, primary_key=True), Column("content", TEXT),
        Column("metadata", JSONB), Column("embedding", Vector(VECTOR_DIMENSION))
    )
    log.info(f"Beginning database transaction for table '{table_name}'.", extra={
        "chunks_to_upsert": len(chunks_to_upsert), "files_to_delete": len(files_to_delete)
    })
    with engine.connect() as connection:
        _validate_table_exists(connection, table_name, log)
        with connection.begin() as transaction:
            try:
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
                        {"id": c.get("id"), "content": c.get("document"), "metadata": c.get("metadata"), "embedding": c.get("embedding")}
                        for c in chunks_to_upsert if c.get("document")
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

# --- 5. FastAPI Application and Global Exception Handlers ---
app = FastAPI(title="RAG Ingestor", version="2.0.4")

@app.exception_handler(ConfigurationError)
async def configuration_exception_handler(request: Request, exc: ConfigurationError):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "type": "configuration_error", "message": str(exc)},
    )

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"status": "error", "type": "validation_error", "message": str(exc)},
    )

@app.get("/")
async def root_health_check():
    """
    Handles the OCI Functions platform health check.
    The platform sends a GET request to the invokeEndpoint to verify the container is ready.
    """
    return {"status": "healthy", "message": "Health check passed"}
    
@app.post("/")
async def handle_invocation(
    request: Request,
    log: logging.LoggerAdapter = Depends(get_logger)
):
    log.info("Function invocation started.")
    try:
        # Step 1: Validate configuration
        try:
            settings = Settings()
        except pydantic.ValidationError as e:
            raise ConfigurationError(f"Invalid configuration: {e}") from e

        # Step 2: Parse the incoming event payload
        body_bytes = await request.body()
        if not body_bytes:
            raise ValueError("Received empty event payload.")
        event = json.loads(body_bytes.decode('utf-8'))
        
        event_data = event.get('data', {})
        bucket_name = event_data.get('additionalDetails', {}).get('bucketName')
        object_name = event_data.get('resourceName')
        if not bucket_name or not object_name:
            raise ValueError("Event data is missing bucketName or resourceName.")
        log.info("Event parsed successfully.", extra={"bucket": bucket_name, "object": object_name})

        # Step 3: Perform the core business logic (the expensive I/O operations)
        engine = _get_db_engine(settings, log)
        payload = _download_and_parse_payload(settings, bucket_name, object_name, log)
        _process_database_transaction(engine, payload, log)
        
        # Step 4: Return a successful response
        invocation_id = log.extra['invocation_id']
        log.info("Function invocation completed successfully.")
        return JSONResponse(
            content={"status": "success", "message": f"Processed {object_name} successfully.", "invocation_id": invocation_id},
            status_code=200
        )

    except (ConfigurationError, ValueError):
        # These are handled by the dedicated exception handlers above.
        # Re-raising them allows the handlers to catch them.
        raise
    except Exception as e:
        log.critical(f"An unhandled exception reached the top-level handler: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "type": "internal_server_error", "message": "An unexpected internal error occurred."},
        )

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "rag-ingestor", "timestamp": datetime.utcnow().isoformat() + "Z"}

# --- 6. Local Development Runner ---
if __name__ == "__main__":
    import uvicorn
    print("--- Starting local development server on http://0.0.0.0:8080 ---")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)