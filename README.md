# RAG Ingestion Function - Deployment Guide v3.0 

This repository contains the source code and deployment instructions for the RAG Ingestion Function. This guide has been updated with battle-tested commands and procedures to ensure a robust and repeatable deployment, incorporating critical lessons learned from production troubleshooting.

## 1. Overview

This is a serverless, event-driven OCI Function responsible for securely and efficiently loading indexed codebase data into a central PostgreSQL database.

### Architecture Flow
```
+------------------+      +----------------------+      +--------------------+      +------------------+
| GitHub Actions   |----->| OCI Object Storage   |----->| OCI Function       |----->| PostgreSQL DB    |
| (CI/CD Producer) |      | (rag-codebase-inbox) |      | (This Function)    |      | (Central DB)     |
+------------------+      +----------------------+      +--------------------+      +------------------+
```

## 2. Prerequisites

Ensure you have the following tools installed and configured:
1.  **OCI CLI:** Installed and configured (`oci setup config`).
2.  **Docker:** Installed and running.
3.  **Fn Project CLI:** Installed.
4.  **jq:** A command-line JSON processor.

---

## 3. Step-by-Step Deployment Guide

### Phase 0: Environment Setup

Execute this block first to set up all necessary variables for your shell session.

```bash
# --- Set these variables with your specific OCI details ---
export TENANCY_OCID="<YOUR_TENANCY_OCID>"
export COMPARTMENT_ID="<YOUR_COMPARTMENT_OCID>"
export SUBNET_ID="<YOUR_SUBNET_ID_FOR_THE_FUNCTION>"
export DB_SECRET_OCID="<YOUR_DB_CONNECTION_SECRET_OCID>"
export VAULT_KEY_OCID="<YOUR_VAULT_MASTER_ENCRYPTION_KEY_OCID>"

# --- These names are defined by the architecture ---
export APP_NAME="rag-ecosystem-app"
export BUCKET_NAME="rag-codebase-inbox"

# --- Set these variables for Docker and Fn CLI login ---
export OCI_REGION="<your_oci_region_e.g.,_eu-frankfurt-1>"
export OCI_REGION_KEY="<your_oci_region_key_e.g.,_fra>"
export OCI_TENANCY_NAMESPACE="<your_tenancy_object_storage_namespace>"
```

### Phase 1: Provision Core Infrastructure

These commands create the bucket and the Functions Application that will host our code.

```bash
# 1.1. Create the Object Storage "Inbox" Bucket
echo "Creating Object Storage bucket: $BUCKET_NAME..."
oci os bucket create \
  --compartment-id $COMPARTMENT_ID \
  --name $BUCKET_NAME \
  --object-events-enabled true

# 1.2. Create the OCI Function Application
echo "Creating Functions Application: $APP_NAME..."
oci fn application create \
  --compartment-id $COMPARTMENT_ID \
  --display-name $APP_NAME \
  --subnet-ids "[\"$SUBNET_ID\"]"
```

### Phase 2: Configure Permissions & Networking

This is the most critical phase. These policies grant all necessary permissions for the system to operate.

```bash
# 2.1. Create the IAM Dynamic Group for the Function
# This group identifies our function so we can grant it permissions.
echo "Creating Dynamic Group..."
oci iam dynamic-group create \
  --name "RAGIngestorFunctionDynamicGroup" \
  --description "Dynamic group for all functions in the RAG application" \
  --matching-rule "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT_ID'}"

# 2.2. Create the Function's Core Permissions Policy
# This policy MUST be created in the ROOT compartment (tenancy) to correctly
# reference the dynamic group (in the root domain) and grant it permissions
# on resources in your project compartment.
echo "Creating Function Permissions Policy in ROOT compartment..."
printf '[
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to read secret-bundles in compartment id %s where target.secret.id = ''%s''",
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to use keys in compartment id %s where target.key.id = ''%s''",
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to read objects in compartment id %s where target.bucket.name = ''%s''",
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to use virtual-network-family in compartment id %s"
]' "${COMPARTMENT_ID}" "${DB_SECRET_OCID}" "${COMPARTMENT_ID}" "${VAULT_KEY_OCID}" "${COMPARTMENT_ID}" "${BUCKET_NAME}" "${COMPARTMENT_ID}" > /tmp/statements.json

oci iam policy create \
  --compartment-id "${TENANCY_OCID}" \
  --name "RAGIngestorFunctionPermissionsPolicy" \
  --description "Grants RAG Ingestor function permissions to decrypt secrets, read objects, and use the VCN." \
  --statements file:///tmp/statements.json

# 2.3. Create the Event Service Invocation Policy
# This allows the OCI Events service to invoke your function.
echo "Creating Event Service Invocation Policy..."
printf '["Allow service events to use fn-function in compartment id %s"]' "${COMPARTMENT_ID}" > /tmp/statements.json

oci iam policy create \
  --compartment-id "${COMPARTMENT_ID}" \
  --name "PlatformEventsToFunctionInvokePolicy" \
  --description "Allows the OCI Events service to invoke functions within this compartment." \
  --statements file:///tmp/statements.json

rm /tmp/statements.json
```

### Phase 3: Deploy and Configure the Function

```bash
# 3.1. Log in to OCI Container Registry (OCIR)
# Get an Auth Token from your OCI user profile page.
echo "<YOUR_AUTH_TOKEN>" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/<your-oci-username> --password-stdin

# 3.2. Configure the Fn CLI Context
fn create context oci-prod --provider oracle
fn use context oci-prod
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
fn update context registry "${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}"

# 3.3. Deploy the function
fn deploy --app ${APP_NAME}

# 3.4. Configure the function with its required environment variables
fn config function ${APP_NAME} rag-ingestor DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} rag-ingestor OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
```

### Phase 4: Create the Event Rule Trigger

Using the UI is the most reliable method for this step.
1.  In the OCI Console, navigate to **Observability & Management -> Events Service -> Rules**.
2.  Click **Create Rule**.
3.  **Rule Conditions:**
    *   **Condition:** `Event Type`
    *   **Service Name:** `Object Storage`
    *   **Event Type:** `Object - Create`
    *   **Attribute:** `bucketName` = `rag-codebase-inbox`
4.  **Actions:**
    *   **Action Type:** `Functions`
    *   Select your compartment, `rag-ecosystem-app` application, and `rag-ingestor` function.
5.  Click **Create Rule**.

**Deployment is now complete and the system is LIVE.**

---

## 4. Operations and Troubleshooting

### End-to-End Test

1.  **On your database host:** Create a test table.
    ```bash
    docker exec postgres-db psql -U platform_admin -d librarian_db -c "CREATE TABLE IF NOT EXISTS test_collection (id TEXT PRIMARY KEY, content TEXT, metadata JSONB, embedding vector(3));"
    ```
2.  **On your deployment host:** Create and upload a test file.
    ```bash
    echo '{"table_name": "test_collection", "chunks_to_upsert": [{"id": "test-id-123", "document": "def test(): pass", "metadata": {"source": "test.py"}, "embedding": [0.1,0.2,0.3]}]}' | gzip > /tmp/test.json.gz
    oci os object put --bucket-name ${BUCKET_NAME} --file /tmp/test.json.gz --name "manual-test.json.gz"
    ```
3.  **On your database host:** Wait 30 seconds, then verify the data.
    ```bash
    sleep 30
    docker exec postgres-db psql -U librarian_user -d librarian_db -c "SELECT * FROM test_collection;"
    ```

### **Proactive Troubleshooting: The Diagnostic Flow**

If the end-to-end test fails, follow this diagnostic flow.

**Step 1: Check the Function Logs (The Function's Story)**

This is your primary source of truth.
1.  Go to OCI Console -> **Developer Services** -> **Functions**.
2.  Navigate to your application (`rag-ecosystem-app`) and function (`rag-ingestor`).
3.  Click on **Logs**. Set the time filter to the last 5 minutes.

*   **If you see new logs with a `502` or other error code:**
    *   The trigger is working, but the function's code is failing.
    *   Find the JSON log entry with `"level": "CRITICAL"`.
    *   Look at the `"invocation_id"`. Copy it.
    *   Filter the logs by this `invocation_id` to see the entire story of that single execution.
    *   Examine the `"exception"` object in the critical log. The `"type"` and `"message"` will tell you exactly what went wrong (e.g., `OperationalError: connection timed out`, `ValidationError`, etc.).

*   **If you see NO new logs at all:**
    *   The function is **not being invoked**. The problem is the trigger mechanism.
    *   Go to **Events Service -> Rules**. Verify your rule is **Active**.
    *   Verify the conditions (Event Type, Service Name, bucketName) are correct.
    *   Check the **Audit** logs for failed `invokeFunction` events, which indicates an IAM policy issue with the Events service itself.