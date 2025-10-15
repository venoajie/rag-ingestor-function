import base64
import json
import logging
import os
import tempfile
import uuid
import traceback
from contextlib import asynccontextmanager
from typing import Annotated

import oci
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
# --- THE FIX IS HERE ---
# JSONResponse is located in the fastapi.responses submodule.
from fastapi.responses import JSONResponse
# --- END OF FIX ---
from psycopg_pool import AsyncConnectionPool

# --- Constants for Configuration ---
REQUIRED_AUTH_VARS = [
    "OCI_USER_OCID", "OCI_FINGERPRINT", "OCI_TENANCY_OCID",
    "OCI_REGION", "OCI_PRIVATE_KEY_B64"
]

# --- Logging Setup ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": record.created,
            "level": record.levelname,
            "message": record.getMessage(),
            "invocation_id": getattr(record, 'invocation_id', 'N/A'),
        }
        if record.exc_info:
            log_record['exception'] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(log_record)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False

# --- Global Clients & Connection Pool ---
object_storage_client = None
secrets_client = None
db_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global object_storage_client, secrets_client, db_pool
    log = logging.LoggerAdapter(logger, {'invocation_id': 'startup'})
    log.info("--- LIFESPAN START: INITIALIZING WITH API KEY AUTH ---")

    key_file_path = None
    try:
        config_values = {key: os.getenv(key) for key in REQUIRED_AUTH_VARS}
        missing_vars = [key for key, value in config_values.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing required OCI auth env vars: {', '.join(missing_vars)}")

        b64_key_content = config_values["OCI_PRIVATE_KEY_B64"]
        decoded_key_bytes = base64.b64decode(b64_key_content)
        private_key_content = decoded_key_bytes.decode('utf-8')

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".pem") as key_file:
            key_file.write(private_key_content)
            key_file_path = key_file.name

        config = {
            "user": config_values["OCI_USER_OCID"], "key_file": key_file_path,
            "fingerprint": config_values["OCI_FINGERPRINT"], "tenancy": config_values["OCI_TENANCY_OCID"],
            "region": config_values["OCI_REGION"]
        }
        oci.config.validate_config(config)
        log.info("OCI config successfully validated.")

        object_storage_client = oci.object_storage.ObjectStorageClient(config=config)
        secrets_client = oci.secrets.SecretsClient(config=config)
        log.info("OCI clients initialized.")

        db_secret_ocid = os.getenv('DB_SECRET_OCID')
        if not db_secret_ocid:
            raise ValueError("Missing critical configuration: DB_SECRET_OCID")

        secret_bundle = secrets_client.get_secret_bundle(secret_id=db_secret_ocid)
        secret_content = secret_bundle.data.secret_bundle_content.content
        decoded_secret = base64.b64decode(secret_content).decode('utf-8')
        db_creds = json.loads(decoded_secret)
        log.info("Database secret retrieved from Vault.")

        conn_info = (
            f"host={db_creds['host']} port={db_creds['port']} "
            f"dbname={db_creds['dbname']} user={db_creds['username']} "
            f"password={db_creds['password']}"
        )
        db_pool = AsyncConnectionPool(conninfo=conn_info, min_size=1, max_size=5)
        log.info("Database connection pool initialized.")
        log.info("--- LIFESPAN SUCCESS: ALL DEPENDENCIES INITIALIZED ---")

    except Exception as e:
        log.critical(f"--- FATAL LIFESPAN CRASH: {e}", exc_info=True)
        raise
    finally:
        if key_file_path and os.path.exists(key_file_path):
            os.remove(key_file_path)

    yield
    if db_pool:
        await db_pool.close()

# --- FastAPI Dependency Injection and Application ---
def get_logger(fn_invoke_id: Annotated[str | None, Header(alias="fn-invoke-id")] = None) -> logging.LoggerAdapter:
    invocation_id = fn_invoke_id or str(uuid.uuid4())
    return logging.LoggerAdapter(logger, {'invocation_id': invocation_id})

def get_os_client():
    if object_storage_client is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: OCI client not initialized.")
    return object_storage_client

async def get_db_connection():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Service Unavailable: DB pool not initialized.")
    async with db_pool.connection() as conn:
        yield conn

app = FastAPI(title="RAG Ingestor", docs_url=None, redoc_url=None, lifespan=lifespan)

@app.post("/call")
async def handle_invocation(
    os_client: Annotated[oci.object_storage.ObjectStorageClient, Depends(get_os_client)],
    log: Annotated[logging.LoggerAdapter, Depends(get_logger)],
    db_conn: Annotated[psycopg.AsyncConnection, Depends(get_db_connection)]
):
    log.info("Invocation received.")
    try:
        oci_namespace = os.getenv('OCI_NAMESPACE')
        target_bucket = os.getenv('TARGET_BUCKET_NAME', 'rag-codebase-inbox')

        if not oci_namespace:
            raise KeyError("OCI_NAMESPACE not set")

        object_name = f"ingest-receipt-{log.extra['invocation_id']}.txt"
        file_content = f"Receipt for invocation {log.extra['invocation_id']}."

        os_client.put_object(
            namespace_name=oci_namespace,
            bucket_name=target_bucket,
            object_name=object_name,
            put_object_body=file_content.encode('utf-8')
        )
        return JSONResponse(
            status_code=200,
            content={ "status": "success", "message": "Object written to bucket." }
        )
    except oci.exceptions.ServiceError as e:
        log.error(f"OCI Service Error: {e.status} {e.message}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"OCI Error: {e.message}")
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")