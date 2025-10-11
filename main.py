
import os
import base64
import gzip
import io
import json
import logging
import re
import tempfile
import textwrap
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
REQUIRED_AUTH_VARS = [
    "OCI_USER_OCID", "OCI_FINGERPRINT", "OCI_TENANCY_OCID",
    "OCI_REGION", "OCI_PRIVATE_KEY_CONTENT"
]
PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
PEM_FOOTER = "-----END RSA PRIVATE KEY-----"

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
    if db_engine is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: Database connection is not ready.")
    return db_engine

def get_os_client() -> oci.object_storage.ObjectStorageClient:
    if object_storage_client is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: Object Storage client is not ready.")
    return object_storage_client

def get_settings() -> Settings:
    if app_settings is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: Application settings are not ready.")
    return app_settings

# --- 5. FastAPI Application & Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_engine, object_storage_client, app_settings
    startup_log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    startup_log.info("--- RAG INGESTOR v2.5 (Robust Auth) LIFESPAN START ---")
    key_file_path = None

    try:
        env_string = repr(os.environ)
        def _get_config_from_env_str(key: str, env_str: str) -> str | None:
            match = re.search(f"'{re.escape(key)}': '([^']*)'", env_str)
            return match.group(1) if match else None

        app_settings = Settings()
        startup_log.info("Application settings loaded.")

        config_values = {key: _get_config_from_env_str(key, env_string) for key in REQUIRED_AUTH_VARS}
        missing_vars = [key for key, value in config_values.items() if not value]
        if missing_vars:
            raise ConfigurationError(f"Missing OCI auth config variables: {missing_vars}")

        # --- MODIFICATION: Robust private key handling ---
        private_key_content_raw = config_values["OCI_PRIVATE_KEY_CONTENT"]
        if PEM_HEADER in private_key_content_raw:
            startup_log.info("Full PEM content detected in private key variable. Using as-is.")
            private_key_content = private_key_content_raw
        else:
            startup_log.info("PEM body detected in private key variable. Wrapping with header/footer.")
            base64_body = private_key_content_raw.strip().replace("\n", "")
            wrapped_body = "\n".join(textwrap.wrap(base64_body, 64))
            private_key_content = f"{PEM_HEADER}\n{wrapped_body}\n{PEM_FOOTER}\n"
        # --- END MODIFICATION ---

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".pem") as key_file:
            key_file.write(private_key_content)
            key_file_path = key_file.name

        oci_config = {
            "user": config_values["OCI_USER_OCID"], "key_file": key_file_path,
            "fingerprint": config_values["OCI_FINGERPRINT"], "tenancy": config_values["OCI_TENANCY_OCID"],
            "region": config_values["OCI_REGION"]
        }
        oci.config.validate_config(oci_config)
        startup_log.info("OCI API Key configuration validated.")

        object_storage_client = oci.object_storage.ObjectStorageClient(config=oci_config, retry_strategy=standard_retry_strategy)
        secrets_client = oci.secrets.SecretsClient(config=oci_config, retry_strategy=standard_retry_strategy)
        startup_log.info("OCI clients initialized.")
        
        secret_bundle = secrets_client.get_secret_bundle(secret_id=app_settings.DB_SECRET_OCID, stage="LATEST")
        secret_content = base64.b64decode(secret_bundle.data.secret_bundle_content.content).decode('utf-8')
        db_config = DbSecret.model_validate(json.loads(secret_content))
        
        db_connection_string = f"postgresql+psycopg://{db_config.username}:{db_config.password.get_secret_value()}@{db_config.host}:{db_config.port}/{db_config.dbname}"
        
        engine = create_engine(db_connection_string, pool_pre_ping=True, connect_args={"connect_timeout": 10})
        db_engine = engine
        startup_log.info("Database engine configured. Connection deferred to first use.")

        startup_log.info("--- ALL DEPENDENCIES INITIALIZED SUCCESSFULLY ---")

    except Exception as e:
        startup_log.critical(f"FATAL: Could not initialize dependencies during startup. Error: {e}", exc_info=True)
        raise
    finally:
        if key_file_path and os.path.exists(key_file_path):
            os.remove(key_file_path)

    yield
    
    if db_engine:
        db_engine.dispose()
        startup_log.info("--- DATABASE CONNECTION POOL CLOSED ---")

app = FastAPI(title="RAG Ingestor", version="2.5.0", docs_url=None, redoc_url=None, lifespan=lifespan)

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
            raise ValueError(f"Source object not found: {object_name}") from e
        raise
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in source object: {object_name}") from e

def _validate_table_exists(connection: 'Connection', table_name: str, log: logging.LoggerAdapter):
    if not re.match(r'^codebase_collection_[a-zA-Z0-9_]+$', table_name):
        raise ValueError(f"Payload provides a syntactically invalid table name: {table_name}")
    query = text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :table_name")
    if connection.execute(query, {"table_name": table_name}).scalar_one_or_none() is None:
        raise ValueError(f"Attempted to ingest data for non-existent table: {table_name}.")
    log.info("Table validation successful.", table_name=table_name)

def _process_database_transaction(engine: Engine, payload: dict, log: logging.LoggerAdapter):
    table_name = payload.get("table_name")
    if not table_name:
        raise ValueError("Payload is missing required 'table_name' field.")
    
    chunks_to_upsert = payload.get("chunks_to_upsert", [])
    files_to_delete = payload.get("files_to_delete", [])
    metadata_obj = MetaData()
    target_table = Table(table_name, metadata_obj, Column("id", PG_UUID, primary_key=True), Column("content", TEXT), Column("metadata", JSONB), Column("embedding", Vector(VECTOR_DIMENSION)))
    
    log.info(f"Beginning database transaction for table '{table_name}'.", extra={"upsert_count": len(chunks_to_upsert), "delete_count": len(files_to_delete)})
    with engine.connect() as connection:
        _validate_table_exists(connection, table_name, log)
        with connection.begin() as transaction:
            try:
                if files_to_delete:
                    vals = values(column("source_file", TEXT), name="files_to_delete_values").data([(f,) for f in files_to_delete])
                    delete_stmt = delete(target_table).where(target_table.c.metadata['source'].astext == vals.c.source_file)
                    connection.execute(delete_stmt)
                if chunks_to_upsert:
                    source_files = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    if source_files:
                        update_vals = values(column("source_file", TEXT), name="files_to_update_values").data([(f,) for f in source_files])
                        connection.execute(delete(target_table).where(target_table.c.metadata['source'].astext == update_vals.c.source_file))
                    
                    records = [{"id": c.get("id"), "content": c.get("document"), "metadata": c.get("metadata"), "embedding": c.get("embedding")} for c in chunks_to_upsert if c.get("document")]
                    if records:
                        connection.execute(insert(target_table), records)
                transaction.commit()
                log.info("Transaction committed successfully.")
            except exc.SQLAlchemyError as e:
                transaction.rollback()
                raise

@app.exception_handler(ConfigurationError)
async def configuration_exception_handler(request: Request, exc: ConfigurationError):
    return JSONResponse(status_code=500, content={"status": "error", "type": "configuration_error", "message": str(exc)})

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"status": "error", "type": "validation_error", "message": str(exc)})

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
        event = await request.json()
        event_data = event.get('data', {})
        bucket_name = event_data.get('additionalDetails', {}).get('bucketName')
        object_name = event_data.get('resourceName')
        if not bucket_name or not object_name:
            raise ValueError("Event data is missing bucketName or resourceName.")
        
        payload = _download_and_parse_payload(os_client, settings, bucket_name, object_name, log)
        _process_database_transaction(engine, payload, log)
        
        log.info("Function invocation completed successfully.")
        return JSONResponse(content={"status": "success", "message": f"Processed {object_name} successfully."}, status_code=200)
    except Exception as e:
        log.critical(f"Unhandled exception: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "type": "internal_server_error", "message": "An unexpected internal error occurred."})

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "rag-ingestor", "timestamp": datetime.utcnow().isoformat() + "Z"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)