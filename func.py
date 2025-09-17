
import io
import os
import json
import logging
import gzip
import re
import oci
from sqlalchemy import create_engine, text, exc

# Configure structured logging for better observability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Global engine object to be reused across warm invocations for performance
db_engine = None

def _get_db_engine():
    """
    Initializes and returns a resilient, cached SQLAlchemy engine.
    Uses OCI Resource Principals for secure, keyless authentication to the Vault.
    """
    global db_engine
    if db_engine is not None:
        return db_engine

    try:
        logger.info("Initializing database engine for the first time.")
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)

        secret_ocid = os.environ.get("DB_SECRET_OCID")
        if not secret_ocid:
            raise ValueError("CRITICAL: DB_SECRET_OCID environment variable not set.")

        logger.info(f"Fetching database connection secret from Vault: {secret_ocid}")
        secret_bundle = secrets_client.get_secret_bundle(secret_id=secret_ocid)
        db_connection_string = secret_bundle.data.secret_bundle_content.content.decode('utf-8')

        # Create a resilient engine with connection pooling and pre-ping to handle transient network issues
        db_engine = create_engine(
            db_connection_string,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800 # Recycle connections every 30 minutes
        )
        logger.info("Database engine initialized successfully.")
        return db_engine
    except Exception as e:
        logger.critical(f"FATAL: Failed to initialize database engine: {e}", exc_info=True)
        raise

def _parse_event(event_data: dict) -> tuple[str, str]:
    """ROBUSTNESS: Safely parses the OCI event data using .get() to prevent KeyErrors."""
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
    
    #namespace = object_storage_client.get_namespace().data

    # Read the namespace from a configuration variable instead of auto-detecting it.
    #why?
    #There is a known edge case: if the user identity associated with the Resource Principal (the function itself) has a default compartment set to something *other* than the root, the `get_namespace()` call can sometimes behave unexpectedly or be affected by tenancy-level policies.
    
    #The error message is a permissions error disguised as a "not found" error. The function has permission to `manage objects`, but it might be failing at the `get_namespace` step before it even tries to get the object.
    
    # Additional deployment step:
    #### Add the Namespace to the Function's Configuration**
    # Now we need to tell the function what its namespace is. 
    # --- Configuration ---
    #export APP_NAME="rag-ecosystem-app"
    # This is your Object Storage namespace from the error log
    #export OCI_NAMESPACE="frpowqeyehes" 
    
    #fn config function $APP_NAME rag-ingestor OCI_NAMESPACE "$OCI_NAMESPACE"

    namespace = os.environ.get("OCI_NAMESPACE")
    if not namespace:
        raise ValueError("CRITICAL: OCI_NAMESPACE environment variable not set.")
        
    logger.info(f"Downloading object '{object_name}' from bucket '{bucket_name}' in namespace '{namespace}'...")
    get_obj = object_storage_client.get_object(namespace, bucket_name, object_name)

    with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
        payload = json.load(gz_file)
    
    logger.info("Successfully downloaded and parsed payload.")
    return payload
    
def _process_database_transaction(engine, payload: dict):
    """
    Handles the core database logic within a single, atomic transaction.
    Performs deletions and upserts idempotently.
    """
    table_name_raw = payload.get("table_name")
    chunks_to_upsert = payload.get("chunks_to_upsert", [])
    files_to_delete = payload.get("files_to_delete", [])

    # SECURITY: Sanitize table_name to prevent SQL injection. Only allow valid characters.
    if not table_name_raw or not re.match(r'^[a-zA-Z0-9_]+$', table_name_raw):
        raise ValueError(f"Payload provides an invalid or missing 'table_name': {table_name_raw}")
    table_name = table_name_raw # Now considered safe

    logger.info(
        f"Beginning database transaction for table '{table_name}'",
        extra={
            "chunks_to_upsert": len(chunks_to_upsert),
            "files_to_delete": len(files_to_delete)
        }
    )

    with engine.connect() as connection:
        with connection.begin() as transaction:
            try:
                # Step 1: Delete records for any source files that were completely removed.
                if files_to_delete:
                    logger.info(f"Deleting records for {len(files_to_delete)} deleted source files.")
                    delete_stmt = text(f"""
                        DELETE FROM {table_name}
                        WHERE (metadata->>'source') IN :files_to_delete
                    """)
                    connection.execute(delete_stmt, {"files_to_delete": tuple(files_to_delete)})

                if chunks_to_upsert:
                    # Step 2: Ensure idempotency by deleting all existing chunks for the files being updated.
                    source_files_to_update = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                    logger.info(f"Upserting records for {len(source_files_to_update)} new/modified source files.")
                    
                    upsert_delete_stmt = text(f"""
                        DELETE FROM {table_name}
                        WHERE (metadata->>'source') IN :source_files
                    """)
                    connection.execute(upsert_delete_stmt, {"source_files": tuple(source_files_to_update)})

                    # Step 3: Insert the new records.
                    insert_stmt = text(f"""
                        INSERT INTO {table_name} (id, content, metadata, embedding)
                        VALUES (:id, :content, :metadata, :embedding)
                    """)
                    
                    # Map the incoming 'document' key to the 'content' database column.
                    records_to_insert = [
                        {
                            "id": chunk.get("id"),
                            "content": chunk.get("document"), # This key comes from the indexer payload
                            "metadata": json.dumps(chunk.get("metadata")), # Ensure metadata is a valid JSON string
                            "embedding": chunk.get("embedding")
                        }
                        for chunk in chunks_to_upsert if chunk.get("document") # Basic validation
                    ]
                    
                    if records_to_insert:
                        connection.execute(insert_stmt, records_to_insert)

                transaction.commit()
                logger.info("Database transaction committed successfully.")
            except exc.SQLAlchemyError as e:
                logger.error(f"Database transaction failed. Rolling back. Error: {e}", exc_info=True)
                transaction.rollback()
                raise

def handler(ctx, data: io.BytesIO = None):
    """
    OCI Function entry point. Orchestrates the ingestion process.
    """
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
        # Re-raise the exception to mark the function invocation as failed.
        # This is crucial for OCI monitoring and the built-in retry mechanism.
        raise