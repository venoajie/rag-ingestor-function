
import os
import io
import json
import gzip
import base64
import unittest.mock
from unittest.mock import MagicMock

# Set environment variables BEFORE importing the function
# These are needed for the Pydantic Settings model
os.environ["DB_SECRET_OCID"] = "ocid1.vaultsecret.oc1..dummy"
os.environ["OCI_NAMESPACE"] = "dummy_namespace"

# Now, import the handler from your main file
from func import handler

def create_mock_oci_clients():
    """Mocks the OCI SDK clients for local testing."""
    
    # --- Mock for OCI Secrets ---
    mock_secrets_client = MagicMock()
    
    # Create the fake secret content (the JSON)
    secret_data = {
        "username": "librarian_user",
        "password": "YOUR_REAL_DATABASE_PASSWORD", # <-- IMPORTANT: Use your real password
        "host": "localhost", # We will connect via the SSH tunnel
        "port": 5433,        # The local port of our SSH tunnel
        "dbname": "librarian_db"
    }
    secret_json = json.dumps(secret_data)
    secret_base64 = base64.b64encode(secret_json.encode('utf-8')).decode('utf-8')

    # Mimic the OCI SDK's response structure
    mock_secret_bundle = MagicMock()
    mock_secret_bundle.data.secret_bundle_content.content = secret_base64
    mock_secrets_client.get_secret_bundle.return_value = mock_secret_bundle

    # --- Mock for OCI Object Storage ---
    mock_os_client = MagicMock()

    # Create a fake data payload
    payload_data = {
        "table_name": "codebase_collection_trading_app_develop", # Use a real table name
        "chunks_to_upsert": [],
        "files_to_delete": ["my-ai-assistant/.git/config", "docker-compose.prod.yml"]
    }
    
    # Gzip the fake payload
    gzipped_payload = io.BytesIO()
    with gzip.GzipFile(fileobj=gzipped_payload, mode='wb') as gz:
        gz.write(json.dumps(payload_data).encode('utf-8'))
    
    # Mimic the OCI SDK's response structure
    mock_object = MagicMock()
    mock_object.data.content = gzipped_payload.getvalue()
    mock_os_client.get_object.return_value = mock_object

    return mock_secrets_client, mock_os_client

def run_local_test():
    """Executes the function handler locally."""
    
    print("--- Starting Local Test ---")

    # Create a mock context object that mimics the FDK
    mock_ctx = MagicMock()
    mock_ctx.Headers.return_value = {"fn-invoke-id": "local-test-run-123"}

    # Create a mock event payload (from Object Storage)
    event_data = {
        "data": {
            "resourceName": "test-payload.json.gz",
            "additionalDetails": {
                "bucketName": "test-bucket"
            }
        }
    }
    event_bytes = io.BytesIO(json.dumps(event_data).encode('utf-8'))

    # Use unittest.mock.patch to intercept the OCI client initializations
    with unittest.mock.patch('oci.secrets.SecretsClient') as MockSecrets, \
         unittest.mock.patch('oci.object_storage.ObjectStorageClient') as MockOS:
        
        # Get our pre-configured mock clients
        mock_secrets_client, mock_os_client = create_mock_oci_clients()
        
        # Tell the patch to return our mocks whenever the clients are created
        MockSecrets.return_value = mock_secrets_client
        MockOS.return_value = mock_os_client

        print("OCI clients mocked. Invoking handler...")
        
        try:
            # Run the actual handler function
            result = handler(mock_ctx, event_bytes)
            print("\n--- Test Result ---")
            print(json.dumps(result, indent=2))
            print("\nSUCCESS: The function executed without errors.")
        except Exception as e:
            print(f"\n--- Test FAILED ---")
            print(f"An exception occurred: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_local_test()