
"""
main.py - OCI Function with FastAPI using API Key Authentication (v2)

This version reads a Base64-encoded private key from the environment,
decodes it at runtime, and writes it to a temporary file. This is the
robust method for handling multi-line secrets.
"""
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
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

# --- Constants for Configuration and Keys ---
# --- THE FIX IS HERE: Point to the Base64-encoded variable ---
REQUIRED_AUTH_VARS = [
    "OCI_USER_OCID", "OCI_FINGERPRINT", "OCI_TENANCY_OCID",
    "OCI_REGION", "OCI_PRIVATE_KEY_B64"
]
# --- END OF FIX ---

# --- Logging Setup (No changes) ---
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
    log.info("--- LIFESPAN START: INITIALIZING WITH B64 API KEY AUTH ---")

    key_file_path = None
    try:
        config_values = {key: os.getenv(key) for key in REQUIRED_AUTH_VARS}
        missing_vars = [key for key, value in config_values.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing required OCI auth env vars: {', '.join(missing_vars)}")

        # --- THE FIX IS HERE: Decode the key from Base64 ---
        b64_key_content = config_values["OCI_PRIVATE_KEY_B64"]
        decoded_key_bytes = base64.b64decode(b64_key_content)
        private_key_content = decoded_key_bytes.decode('utf-8')
        # --- END OF FIX ---

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
        # (Rest of the function is unchanged)
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

# --- FastAPI App (Unchanged) ---
app = FastAPI(title="Hello World Writer", docs_url=None, redoc_url=None, lifespan=lifespan)

@app.post("/call")
async def handle_invocation(log: Annotated[logging.LoggerAdapter, Depends(get_logger)]):
    log.info("Invocation received and startup successful.")
    return JSONResponse(
        status_code=200,
        content={ "status": "success", "message": "API Key authenticated function started successfully." }
    )
