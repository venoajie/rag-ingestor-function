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
            chunks_data = json.load(gz_file)
        logger.info(f"Successfully downloaded and decompressed {object_name}. Found {len(chunks_data)} chunks.")

        # 3. Load Data into Database (Idempotent Transaction into a single table)
        engine = get_db_engine()
        with engine.connect() as connection:
            with connection.begin() as transaction:
                try:
                    # Simple & Robust Idempotency: Delete all records from this source file.
                    logger.info(f"Deleting existing records for source_document: {object_name}")
                    delete_stmt = text("DELETE FROM code_chunks WHERE source_document = :source")
                    connection.execute(delete_stmt, {"source": object_name})

                    logger.info(f"Inserting {len(chunks_data)} new records.")
                    insert_stmt = text("""
                        INSERT INTO code_chunks (source_document, source_file, chunk_text, embedding)
                        VALUES (:source_document, :source_file, :chunk_text, :embedding)
                    """)
                    
                    records_to_insert = [
                        {
                            "source_document": object_name,
                            "source_file": chunk.get("source", "unknown"),
                            "chunk_text": chunk.get("chunk_text", ""),
                            "embedding": chunk.get("embedding", [])
                        }
                        for chunk in chunks_data
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