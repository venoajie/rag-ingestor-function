
# main.py - v2.2 

import os         # <--- ADD THIS LINE
import tempfile   # <--- ADD THIS LINE

import base64
import gzip
import io
import json
import logging
import re
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import oci
import oci.exceptions
import pydantic
import pydantic_settings
from fastapi import FastAPI, Request, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from oci.retry import RetryStrategyBuilder
from sqlalchemy import create_engine, text, exc, Table, MetaData, Column, values, column
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import JSONB, TEXT, UUID as PG_UUID
from sqlalchemy.sql import delete, insert
from pgvector.sqlalchemy import Vector

# --- 0. Application-Specific Constants ---
VECTOR_DIMENSION = 1024

# --- 1. Advanced Structured Logging (No changes) ---
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

# --- 2. Context-Aware Logging (No changes) ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})

# --- 3. Strict Configuration (No changes) ---
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
# Globals to be initialized at startup
db_engine: Engine | None = None
object_storage_client: oci.object_storage.ObjectStorageClient | None = None
app_settings: Settings | None = None

# REFINEMENT: Define a standard, reusable retry strategy for all OCI clients.
standard_retry_strategy = RetryStrategyBuilder().add_max_attempts(4).get_retry_strategy()

def get_db_engine() -> Engine:
    if db_engine is None:
        logger.critical("Database engine is not initialized.")
        raise HTTPException(status_code=503, detail="Service Unavailable: Database connection is not ready.")
    return db_engine

def get_os_client() -> oci.object_storage.ObjectStorageClient:
    if object_storage_client is None:
        logger.critical("Object Storage client is not initialized.")
        raise HTTPException(status_code=503, detail="Service Unavailable: Object Storage client is not ready.")
    return object_storage_client

def get_settings() -> Settings:
    if app_settings is None:
        logger.critical("Application settings are not initialized.")
        raise HTTPException(status_code=503, detail="Service Unavailable: Application settings are not ready.")
    return app_settings

def initialize_dependencies():
    global db_engine, object_storage_client, app_settings
    startup_log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    
    try:
        settings = Settings()
        app_settings = settings
        
        
        # --- DEBUG-ENHANCEMENT 1: Log loaded settings for clarity ---
        startup_log.info("Application settings loaded.", extra={
            "DB_SECRET_OCID": settings.DB_SECRET_OCID,
            "OCI_NAMESPACE": settings.OCI_NAMESPACE
        })
        
    except pydantic.ValidationError as e:
        raise ConfigurationError(f"Invalid configuration during startup: {e}") from e  
    
    
    # --- START TEMPORARY MODIFICATION ---
    signer = None
    oci_config = {}
    oci_config_b64 = os.getenv("OCI_CONFIG_B64")

    if oci_config_b64:
        startup_log.warning("Using temporary user principal auth from OCI_CONFIG_B64.")
        config_content = base64.b64decode(oci_config_b64).decode('utf-8')
        config_json = json.loads(config_content)
        
        # The OCI SDK needs the key as a file, so we write it temporarily
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".pem") as key_file:
            key_file.write(config_json["key_content"])
            key_file_path = key_file.name
        
        oci_config = {
            "user": config_json["user"],
            "key_file": key_file_path,
            "fingerprint": config_json["fingerprint"],
            "tenancy": config_json["tenancy"],
            "region": config_json["region"]
        }
        signer = oci.Signer.from_config(oci_config)
    else:
        startup_log.info("Using resource principal for authentication.")
        signer = oci.auth.signers.get_resource_principals_signer()
    # --- END TEMPORARY MODIFICATION ---
    
    
    
    
    
    startup_log.info("Attempting to initialize OCI signer using Resource Principals.")
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        startup_log.info("Successfully initialized Resource Principals signer.")
    except Exception as e:
        # This is the critical debugging step. We dump the environment.
        import os
        env_vars = {k: v for k, v in os.environ.items() if "OCI" in k or "FN" in k}
        startup_log.critical(
            "FATAL: Failed to get Resource Principals signer. "
            "This almost always means a networking (Service Gateway) or IAM (Dynamic Group/Policy) issue. "
            f"Underlying error: {e}",
            extra={"relevant_environment_variables": env_vars}
        )
        raise ConfigurationError(
            "Could not authenticate with OCI Resource Principals. "
            "Verify VCN Service Gateway and IAM policies."
        ) from e

    # Now use the determined signer and config for all clients
    object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer, retry_strategy=standard_retry_strategy)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
        
        
            startup_log.info(f"Initializing dependencies (attempt {attempt + 1}/{max_retries}).")
            
            # --- DEBUG-ENHANCEMENT 2: Log before the network call to Secrets ---
            startup_log.info("Attempting to fetch secret bundle from OCI Vault.")
            secrets_client = oci.secrets.SecretsClient(config={}, signer=signer, retry_strategy=standard_retry_strategy)
            secret_bundle = secrets_client.get_secret_bundle(secret_id=settings.DB_SECRET_OCID, stage="LATEST")
            secret_content = base64.b64decode(secret_bundle.data.secret_bundle_content.content).decode('utf-8')
            db_config = DbSecret.model_validate(json.loads(secret_content))
            
            # --- DEBUG-ENHANCEMENT 3: Log connection details (password is auto-masked by Pydantic) ---
            startup_log.info("Secret bundle fetched. Preparing database connection.", extra={
                "db_host": db_config.host,
                "db_port": db_config.port,
                "db_user": db_config.username,
                "db_name": db_config.dbname
            })
            
        
            startup_log.info(f"Initializing database engine (attempt {attempt + 1}/{max_retries}).")
            secrets_client = oci.secrets.SecretsClient(config={}, signer=signer, retry_strategy=standard_retry_strategy)
            secret_bundle = secrets_client.get_secret_bundle(secret_id=settings.DB_SECRET_OCID, stage="LATEST")
            secret_content = base64.b64decode(secret_bundle.data.secret_bundle_content.content).decode('utf-8')
            db_config = DbSecret.model_validate(json.loads(secret_content))
            db_connection_string = f"postgresql+psycopg://{db_config.username}:{db_config.password.get_secret_value()}@{db_config.host}:{db_config.port}/{db_config.dbname}"
            engine = create_engine(db_connection_string, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800, connect_args={"connect_timeout": 10, "application_name": "rag-ingestion-fn"})
            with engine.connect() as connection:
                db_version = connection.execute(text("SELECT version()")).scalar()


            
            # --- DEBUG ENHANCEMENT 4##: Log before the network call to the Database ---
            startup_log.info("Attempting to establish and validate database connection.")
            with engine.connect() as connection:
                db_version = connection.execute(text("SELECT version()")).scalar()
            
            
            startup_log.info("Database engine initialized and connection validated.", extra={"db_version": db_version})
            db_engine = engine
            return
        except Exception as e:
            startup_log.error(f"Failed to initialize database engine on attempt {attempt + 1}: {e}", exc_info=True)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                startup_log.critical("All attempts to initialize database engine failed during startup.")
                raise

def _download_and_parse_payload(os_client: oci.object_storage.ObjectStorageClient, settings: Settings, bucket_name: str, object_name: str, log: logging.LoggerAdapter) -> dict:
    log.info(f"Downloading object '{object_name}' from bucket '{bucket_name}'.")
    try:
        get_obj = os_client.get_object(settings.OCI_NAMESPACE, bucket_name, object_name)
        with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
            payload = json.load(gz_file)
        log.info("Successfully downloaded and parsed payload.")
        return payload
    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            log.error(f"Object '{object_name}' not found in bucket '{bucket_name}'.", exc_info=True)
            raise ValueError(f"Source object not found: {object_name}") from e
        log.error(f"An OCI Service Error occurred while downloading '{object_name}'.", exc_info=True)
        raise
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse JSON from object '{object_name}'. The file may be corrupted.", exc_info=True)
        raise ValueError(f"Invalid JSON in source object: {object_name}") from e

# _validate_table_exists and _process_database_transaction remain unchanged

def _validate_table_exists(connection: 'Connection', table_name: str, log: logging.LoggerAdapter):
    if not re.match(r'^codebase_collection_[a-zA-Z0-9_]+$', table_name):
        log.error("Table name failed syntactic validation.", table_name=table_name)
        raise ValueError(f"Payload provides a syntactically invalid table name: {table_name}")
    query = text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :table_name")
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
    target_table = Table(table_name, metadata_obj, Column("id", PG_UUID, primary_key=True), Column("content", TEXT), Column("metadata", JSONB), Column("embedding", Vector(VECTOR_DIMENSION)))
    log.info(f"Beginning database transaction for table '{table_name}'.", extra={"chunks_to_upsert": len(chunks_to_upsert), "files_to_delete": len(files_to_delete)})
    with engine.connect() as connection:
        _validate_table_exists(connection, table_name, log)
        with connection.begin() as transaction:
            try:
                if files_to_delete:
                    log.info(f"Deleting {len(files_to_delete)} source files.")
                    vals = values(column("source_file", TEXT), name="files_to_delete_values").data([(f,) for f in files_to_delete])
                    delete_stmt = delete(target_table).where(target_table.c.metadata['source'].astext == vals.c.source_file)
                    connection.execute(delete_stmt)
                if chunks_to_upsert:
                    source_files_to_update = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    log.info(f"Upserting data for {len(source_files_to_update)} source files.")
                    if source_files_to_update:
                        update_vals = values(column("source_file", TEXT), name="files_to_update_values").data([(f,) for f in source_files_to_update])
                        upsert_delete_stmt = delete(target_table).where(target_table.c.metadata['source'].astext == update_vals.c.source_file)
                        connection.execute(upsert_delete_stmt)
                    records_to_insert = [{"id": c.get("id"), "content": c.get("document"), "metadata": c.get("metadata"), "embedding": c.get("embedding")} for c in chunks_to_upsert if c.get("document")]
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

# --- 5. FastAPI Application ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    startup_log.info("--- RAG INGESTOR v2.2 (Hardened) LIFESPAN START ---")
    try:
        initialize_dependencies()
        startup_log.info("--- ALL DEPENDENCIES INITIALIZED SUCCESSFULLY ---")
    except Exception as e:
        startup_log.critical(f"FATAL: Could not initialize dependencies during startup. Error: {e}", exc_info=True)
        raise
    yield
    if db_engine:
        db_engine.dispose()
        startup_log.info("--- DATABASE CONNECTION POOL CLOSED ---")

app = FastAPI(title="RAG Ingestor", version="2.2.0", docs_url=None, redoc_url=None, lifespan=lifespan)

# Exception handlers remain unchanged
@app.exception_handler(ConfigurationError)
async def configuration_exception_handler(request: Request, exc: ConfigurationError):
    logger.critical(f"Configuration Error: {exc}", extra={'invocation_id': 'config_error'})
    return JSONResponse(status_code=500, content={"status": "error", "type": "configuration_error", "message": str(exc)})

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    logger.error(f"Value Error: {exc}", extra={'invocation_id': 'validation_error'})
    return JSONResponse(status_code=400, content={"status": "error", "type": "validation_error", "message": str(exc)})

@app.get("/")
async def root_health_check():
    return {"status": "healthy", "message": "Health check passed"}

@app.post("/")
async def handle_invocation(
    request: Request,
    log: logging.LoggerAdapter = Depends(get_logger),
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_db_engine),
    os_client: oci.object_storage.ObjectStorageClient = Depends(get_os_client)
):
    log.info("Function invocation started.")
    try:
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
        
        payload = _download_and_parse_payload(os_client, settings, bucket_name, object_name, log)
        _process_database_transaction(engine, payload, log)
        
        invocation_id = log.extra['invocation_id']
        log.info("Function invocation completed successfully.")
        return JSONResponse(content={"status": "success", "message": f"Processed {object_name} successfully.", "invocation_id": invocation_id}, status_code=200)
    except (ConfigurationError, ValueError) as e:
        raise
    except Exception as e:
        log.critical(f"An unhandled exception reached the top-level handler: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "type": "internal_server_error", "message": "An unexpected internal error occurred."})

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "rag-ingestor", "timestamp": datetime.utcnow().isoformat() + "Z"}

if __name__ == "__main__":
    import uvicorn
    print("--- Starting local development server on http://0.0.0.0:8080 ---")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)