
import os
import base64
import gzip
import io
import json
import logging
import re
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import oci
import oci.auth.signers
import oci.exceptions
import pydantic
import pydantic_settings
from fastapi import FastAPI, Request, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from oci.retry import RetryStrategyBuilder
from sqlalchemy import create_engine, text, exc, Table, MetaData, Column, values, column, inspect
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.dialects.postgresql import JSONB, TEXT, UUID as PG_UUID
from sqlalchemy.sql import delete, insert
from pgvector.sqlalchemy import Vector
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# --- 0. Application-Specific Constants ---
VECTOR_DIMENSION = 1024
ALLOWED_TABLE_PREFIX = "codebase_collection_"

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
        if record.exc_info:
            log_record['exception'] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(log_record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False

# --- 2. Context-Aware Logging ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})

# --- 3. Strict Configuration ---
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
object_storage_client: oci.object_storage.ObjectStorageClient | None = None
app_settings: Settings | None = None
standard_retry_strategy = RetryStrategyBuilder().add_max_attempts(4).get_retry_strategy()

def get_db_engine() -> Engine:
    if db_engine is None: raise HTTPException(status_code=503, detail="DB engine not initialized.")
    return db_engine

def get_os_client() -> oci.object_storage.ObjectStorageClient:
    if object_storage_client is None: raise HTTPException(status_code=503, detail="OS client not initialized.")
    return object_storage_client

def get_settings() -> Settings:
    if app_settings is None: raise HTTPException(status_code=503, detail="Settings not initialized.")
    return app_settings

# --- 5. FastAPI Application & Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_engine, object_storage_client, app_settings
    startup_log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    startup_log.info("--- RAG INGESTOR v3.1 (Resource Principal, Hardened) LIFESPAN START ---")

    try:
        app_settings = Settings()
        startup_log.info("Application settings loaded via Pydantic.")

        signer = oci.auth.signers.get_resource_principals_signer()
        startup_log.info("OCI Resource Principal Signer acquired.")

        object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer, retry_strategy=standard_retry_strategy)
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer, retry_strategy=standard_retry_strategy)
        startup_log.info("OCI clients initialized using Resource Principal.")

        secret_bundle = secrets_client.get_secret_bundle(secret_id=app_settings.DB_SECRET_OCID, stage="LATEST")
        secret_content = base64.b64decode(secret_bundle.data.secret_bundle_content.content).decode('utf-8')
        db_config = DbSecret.model_validate(json.loads(secret_content))
        
        db_connection_string = f"postgresql+psycopg://{db_config.username}:{db_config.password.get_secret_value()}@{db_config.host}:{db_config.port}/{db_config.dbname}"
        
        engine = create_engine(db_connection_string, pool_pre_ping=True, connect_args={"connect_timeout": 10})
        db_engine = engine
        startup_log.info("Database engine configured.")

        startup_log.info("--- ALL DEPENDENCIES INITIALIZED SUCCESSFULLY ---")

    except Exception as e:
        startup_log.critical(f"FATAL: Could not initialize dependencies. Error: {e}", exc_info=True)
        raise

    yield
    
    if db_engine:
        db_engine.dispose()
        startup_log.info("--- DATABASE CONNECTION POOL CLOSED ---")

app = FastAPI(title="RAG Ingestor", version="3.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)

# --- The rest of the file (endpoints, helpers) remains unchanged ---
def _download_and_parse_payload(os_client: oci.object_storage.ObjectStorageClient, settings: Settings, bucket_name: str, object_name: str, log: logging.LoggerAdapter) -> dict:
    log.info(f"Downloading object '{object_name}' from bucket '{bucket_name}'.")
    try:
        get_obj = os_client.get_object(settings.OCI_NAMESPACE, bucket_name, object_name)
        with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
            payload = json.load(gz_file)
        return payload
    except oci.exceptions.ServiceError as e:
        if e.status == 404: raise ValueError(f"Source object not found: {object_name}") from e
        raise
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in source object: {object_name}") from e

# REFACTOR: This function safely validates table names against the schema to prevent SQL injection.
def _validate_table_name(connection: Connection, table_name: str, log: logging.LoggerAdapter):
    """
    Safely validates the existence of a table using schema introspection.
    Prevents SQL injection by not using user input to construct SQL.
    """
    if not table_name or not table_name.startswith(ALLOWED_TABLE_PREFIX):
        raise ValueError(f"Invalid table name format: Must start with '{ALLOWED_TABLE_PREFIX}'.")
    
    inspector = inspect(connection)
    if not inspector.has_table(table_name, schema="public"):
        raise ValueError(f"Table does not exist or is not accessible: {table_name}.")
    log.info(f"Table '{table_name}' validated successfully.")

# REFACTOR: Added a retry decorator to handle transient database errors.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(exc.OperationalError),
    reraise=True
)
def _process_database_transaction(engine: Engine, payload: dict, log: logging.LoggerAdapter):
    table_name = payload.get("table_name")
    if not table_name: raise ValueError("Payload missing 'table_name'.")
    
    chunks_to_upsert = payload.get("chunks_to_upsert", [])
    files_to_delete = payload.get("files_to_delete", [])
    metadata_obj = MetaData()
    
    with engine.connect() as connection:
        # REFACTOR: Replaced unsafe regex validation with safe schema introspection.
        _validate_table_name(connection, table_name, log)
        
        target_table = Table(table_name, metadata_obj, Column("id", PG_UUID, primary_key=True), Column("content", TEXT), Column("metadata", JSONB), Column("embedding", Vector(VECTOR_DIMENSION)))
        
        with connection.begin() as transaction:
            try:
                if files_to_delete:
                    # Using a simple loop for clarity and to avoid complex value constructs for deletes.
                    for f in files_to_delete:
                        connection.execute(delete(target_table).where(target_table.c.metadata['source'].astext == f))
                
                if chunks_to_upsert:
                    source_files = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    if source_files:
                        for f in source_files:
                             connection.execute(delete(target_table).where(target_table.c.metadata['source'].astext == f))
                    
                    records = [{"id": c.get("id"), "content": c.get("document"), "metadata": c.get("metadata"), "embedding": c.get("embedding")} for c in chunks_to_upsert if c.get("document")]
                    if records:
                        connection.execute(insert(target_table), records)
                
                transaction.commit()
                log.info(f"Transaction committed successfully for table '{table_name}'.")
            except exc.SQLAlchemyError as e:
                log.error(f"Database transaction failed for table '{table_name}'. Rolling back.", exc_info=True)
                transaction.rollback()
                raise

@app.exception_handler(ConfigurationError)
async def configuration_exception_handler(request: Request, exc: ConfigurationError):
    return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

@app.post("/")
async def handle_invocation(request: Request, log: logging.LoggerAdapter = Depends(get_logger), settings: Settings = Depends(get_settings), engine: Engine = Depends(get_db_engine), os_client: oci.object_storage.ObjectStorageClient = Depends(get_os_client)):
    log.info("Function invocation started.")
    try:
        event = await request.json()
        data = event.get('data', {})
        bucket = data.get('additionalDetails', {}).get('bucketName')
        obj = data.get('resourceName')
        if not bucket or not obj: raise ValueError("Event missing bucketName or resourceName.")
        
        payload = _download_and_parse_payload(os_client, settings, bucket, obj, log)
        _process_database_transaction(engine, payload, log)
        
        log.info("Function invocation completed successfully.")
        return JSONResponse(content={"status": "success", "message": f"Processed {obj}."})
    except Exception as e:
        log.critical(f"Unhandled exception during invocation: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal server error."})

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
