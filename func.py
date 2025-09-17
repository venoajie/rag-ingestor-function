
import io
import os
import json
import logging
import gzip
import re
import oci
from sqlalchemy import create_engine, text, exc

# Configure structured logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Global engine object
db_engine = None

def _get_db_engine():
    """Initializes and returns a resilient, cached SQLAlchemy engine."""
    global db_engine
    if db_engine is not None:
        return db_engine
    try:
        logger.info("Initializing database engine.")
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
        secret_ocid = os.environ.get("DB_SECRET_OCID")
        if not secret_ocid:
            raise ValueError("CRITICAL: DB_SECRET_OCID environment variable not set.")
        logger.info(f"Fetching secret from Vault: {secret_ocid}")
        secret_bundle = secrets_client.get_secret_bundle(secret_id=secret_ocid)
        db_connection_string = secret_bundle.data.secret_bundle_content.content.decode('utf-8')
        db_engine = create_engine(
            db_connection_string, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800
        )
        logger.info("Database engine initialized successfully.")
        return db_engine
    except Exception as e:
        logger.critical(f"FATAL: Failed to initialize database engine: {e}", exc_info=True)
        raise

def _parse_event(event_data: dict) -> tuple[str, str]:
    """Safely parses the OCI event data."""
    data = event_data.get('data', {})
    additional_details = data.get('additionalDetails', {})
    bucket_name = additional_details.get('bucketName')
    object_name = data.get('resourceName')
    if not bucket_name or not object_name:
        raise ValueError(f"Event data is missing bucketName or resourceName. Event: {event_data}")
    return bucket_name, object_name

def _download_and_parse_payload(bucket_name: str, object_name: str) -> dict:
    """Downloads the object from OCI storage, decompresses it, and parses the JSON payload."""
    signer = oci.auth.signers.get_resource_principals_signer()
    object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    
    # THIS IS THE CORRECT CODE: Let the SDK auto-discover the namespace.
    namespace = object_storage_client.get_namespace().data

    logger.info(f"Downloading object '{object_name}' from bucket '{bucket_name}' in namespace '{namespace}'...")
    get_obj = object_storage_client.get_object(namespace, bucket_name, object_name)

    with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
        payload = json.load(gz_file)
    
    logger.info("Successfully downloaded and parsed payload.")
    return payload

def _process_database_transaction(engine, payload: dict):
    """Handles the core database logic within a single, atomic transaction."""
    table_name_raw = payload.get("table_name")
    chunks_to_upsert = payload.get("chunks_to_upsert", [])
    files_to_delete = payload.get("files_to_delete", [])

    if not table_name_raw or not re.match(r'^[a-zA-Z0-9_]+$', table_name_raw):
        raise ValueError(f"Payload provides an invalid or missing 'table_name': {table_name_raw}")
    table_name = table_name_raw

    logger.info(f"Beginning database transaction for table '{table_name}'")

    with engine.connect() as connection:
        with connection.begin() as transaction:
            try:
                if files_to_delete:
                    delete_stmt = text(f"DELETE FROM {table_name} WHERE (metadata->>'source') IN :files_to_delete")
                    connection.execute(delete_stmt, {"files_to_delete": tuple(files_to_delete)})
                if chunks_to_upsert:
                    source_files_to_update = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    upsert_delete_stmt = text(f"DELETE FROM {table_name} WHERE (metadata->>'source') IN :source_files")
                    connection.execute(upsert_delete_stmt, {"source_files": tuple(source_files_to_update)})
                    insert_stmt = text(f"INSERT INTO {table_name} (id, content, metadata, embedding) VALUES (:id, :content, :metadata, :embedding)")
                    records_to_insert = [
                        {"id": chunk.get("id"), "content": chunk.get("document"), "metadata": json.dumps(chunk.get("metadata")), "embedding": chunk.get("embedding")}
                        for chunk in chunks_to_upsert if chunk.get("document")
                    ]
                    if records_to_insert:
                        connection.execute(insert_stmt, records_to_insert)
                transaction.commit()
                logger.info("Transaction committed successfully.")
            except exc.SQLAlchemyError as e:
                logger.error(f"Database transaction failed. Rolling back. Error: {e}", exc_info=True)
                transaction.rollback()
                raise

def handler(ctx, data: io.BytesIO = None):
    """OCI Function entry point."""
    try:
        event = json.loads(data.getvalue().decode('utf-8'))
        bucket_name, object_name = _parse_event(event)
        logger.info(f"Processing event for object: {object_name} in bucket: {bucket_name}")
        payload = _download_and_parse_payload(bucket_name, object_name)
        engine = _get_db_engine()
        _process_database_transaction(engine, payload)
        return {"status": "success", "message": f"Processed {object_name} successfully."}
    except Exception as e:
        logger.error(f"An unhandled error occurred in the handler: {e}", exc_info=True)
        raise