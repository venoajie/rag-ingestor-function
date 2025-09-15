import io
import os
import json
import logging
import gzip
import oci
from sqlalchemy import create_engine, text, exc

# Configure structured logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Global engine object to be reused across invocations
db_engine = None

def get_db_engine():
    """Initializes and returns a resilient SQLAlchemy engine."""
    global db_engine
    if db_engine is not None:
        return db_engine

    try:
        logger.info("Initializing database engine.")
        # Best Practice: Use Resource Principals for secure, keyless authentication.
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)

        secret_ocid = os.environ.get("DB_SECRET_OCID")
        if not secret_ocid:
            raise ValueError("DB_SECRET_OCID environment variable not set.")

        logger.info(f"Fetching secret from Vault: {secret_ocid}")
        secret_bundle = secrets_client.get_secret_bundle(secret_id=secret_ocid)
        db_connection_string = secret_bundle.data.secret_bundle_content.content.decode('utf-8')

        # Create a resilient engine with connection pooling and pre-ping
        db_engine = create_engine(
            db_connection_string,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10
        )
        logger.info("Database engine initialized successfully.")
        return db_engine
    except Exception as e:
        logger.error(f"Failed to initialize database engine: {e}", exc_info=True)
        raise

def handler(ctx, data: io.BytesIO = None):
    """OCI Function entry point."""
    try:
        # 1. Parse Event Data (Reliable Method)
        event = json.loads(data.getvalue().decode('utf-8'))
        bucket_name = event['data']['additionalDetails']['bucketName']
        object_name = event['data']['resourceName']
        logger.info(f"Processing event for object: {object_name} in bucket: {bucket_name}")

        # 2. Download and Decompress File
        signer = oci.auth.signers.get_resource_principals_signer()
        object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        namespace = object_storage_client.get_namespace().data
        get_obj = object_storage_client.get_object(namespace, bucket_name, object_name)

        with gzip.GzipFile(fileobj=io.BytesIO(get_obj.data.content), mode='rb') as gz_file:
        # 1. Correctly parse the entire payload object
            payload = json.load(gz_file)

        table_name = payload.get("table_name")
        chunks_to_upsert = payload.get("chunks_to_upsert", [])
        files_to_delete = payload.get("files_to_delete", [])

        if not table_name:
            raise ValueError("Payload is missing the required 'table_name' field.")

        logger.info(
            f"Processing payload for table '{table_name}'",
            chunks_to_upsert=len(chunks_to_upsert),
            files_to_delete=len(files_to_delete)
        )

        engine = get_db_engine()
        with engine.connect() as connection:
            with connection.begin() as transaction:
                try:
                    # 2. Correct Deletion Logic: Use the provided list of deleted files
                    if files_to_delete:
                        logger.info(f"Deleting records for {len(files_to_delete)} deleted source files.")
                        # Use a parameterized IN clause for security and efficiency
                        delete_stmt = text(f"""
                            DELETE FROM {table_name}
                            WHERE (metadata->>'source') IN :files_to_delete
                        """)
                        connection.execute(delete_stmt, {"files_to_delete": tuple(files_to_delete)})

                    if chunks_to_upsert:
                        # Idempotency: Delete all chunks for the source files we are about to update.
                        # This handles both new files and modified files in one step.
                        source_files_to_update = list(set(c['metadata']['source'] for c in chunks_to_upsert))
                        logger.info(f"Upserting records for {len(source_files_to_update)} new/modified source files.")
                        
                        upsert_delete_stmt = text(f"""
                            DELETE FROM {table_name}
                            WHERE (metadata->>'source') IN :source_files
                        """)
                        connection.execute(upsert_delete_stmt, {"source_files": tuple(source_files_to_update)})

                        # 3. Correct Insert Logic with correct field mapping
                        insert_stmt = text(f"""
                            INSERT INTO {table_name} (id, content, metadata, embedding)
                            VALUES (:id, :content, :metadata, :embedding)
                        """)
                        
                        records_to_insert = [
                            {
                                "id": chunk.get("id"),
                                "content": chunk.get("document"),
                                "metadata": json.dumps(chunk.get("metadata")), # Ensure metadata is a JSON string for JSONB
                                "embedding": chunk.get("embedding")
                            }
                            for chunk in chunks_to_upsert
                        ]
                        
                        if records_to_insert:
                            connection.execute(insert_stmt, records_to_insert)

                    transaction.commit()
                    logger.info("Transaction committed successfully.")
                except exc.SQLAlchemyError as e:
                    logger.error(f"Database transaction failed. Rolling back. Error: {e}", exc_info=True)
                    transaction.rollback()
                    raise
            
        return {"status": "success", "message": f"Processed {object_name} successfully."}
    except Exception as e:
        logger.error(f"An unhandled error occurred: {e}", exc_info=True)
        raise