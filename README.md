# OCI Serverless Function: RAG Ingestor - v4.0

This repository contains the source code and the definitive, battle-tested deployment guide for the `rag-ingestor` OCI Function. This document has been hardened with lessons learned from production incidents to ensure a robust and repeatable deployment.

## 1. Architectural Overview

This function is a critical component of the RAG ecosystem. It is a serverless, event-driven service responsible for securely and efficiently loading indexed codebase data into a central PostgreSQL database.

**Data Flow:**
```
+------------------+      +----------------------+      +--------------------+      +------------------+
| GitHub Actions   |----->| OCI Object Storage   |----->| OCI Function       |----->| PostgreSQL DB    |
| (CI/CD Producer) |      | (rag-codebase-inbox) |      | (This Function)    |      | (Central DB)     |
+------------------+      +----------------------+      +--------------------+      +------------------+
```

**Core Technology:** This function is implemented as a self-contained **FastAPI application** running in a custom Docker container.

**Architectural Decision (PB-20250924-01):** The decision to bypass the official Oracle Python FDK (`fdk`) was made after a critical, unfixable build deadlock was discovered. The `fdk`'s dependency on `httptools==0.4.0` is fundamentally incompatible with the C-API of Python 3.11+. This "FDK-less" approach resolves the build deadlock, unlocks the use of modern Python versions, and improves local testability.

## 2. Prerequisites

Ensure you have the following tools installed and configured:
1.  **OCI CLI:** Installed and configured (`oci setup config`).
2.  **Docker:** Installed and running.
3.  **Fn Project CLI:** Installed.
4.  **jq:** A command-line JSON processor.

---

## 3. Step-by-Step Deployment Guide

This guide is designed to be executed as a script.

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
export FUNCTION_NAME="rag-ingestor"
export BUCKET_NAME="rag-codebase-inbox"
export DYNAMIC_GROUP_NAME="RAGIngestorFunctionDynamicGroup"

# --- Set these variables for Docker and Fn CLI login ---
export OCI_REGION="<your_oci_region_e.g.,_eu-frankfurt-1>"
export OCI_REGION_KEY="<your_oci_region_key_e.g.,_fra>"
export OCI_TENANCY_NAMESPACE="<your_tenancy_object_storage_namespace>"
export OCI_USERNAME="<your_oci_username_for_docker_login>"
```

### Phase 1: Provision Core Infrastructure

These commands create the bucket and the Functions Application that will host our code. They are idempotent; running them again will not cause errors if the resources already exist.

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

### Phase 2: Configure Permissions & Networking (IAM)

This is the most critical phase. These policies grant all necessary permissions for the system to operate.

```bash
# 2.1. Create the IAM Dynamic Group for the Function
# This group identifies our function so we can grant it permissions.
echo "Creating Dynamic Group: $DYNAMIC_GROUP_NAME..."
oci iam dynamic-group create \
  --name "$DYNAMIC_GROUP_NAME" \
  --description "Dynamic group for all functions in the RAG application compartment" \
  --matching-rule "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT_ID'}"

# 2.2. Create the Function's Core Permissions Policy
# This policy MUST be created in the ROOT compartment (tenancy) to correctly
# reference the dynamic group and grant it permissions on resources in your project compartment.
echo "Creating Function Permissions Policy in ROOT compartment..."
printf '[
  "Allow dynamic-group %s to read secret-bundles in compartment id %s where target.secret.id = ''%s''",
  "Allow dynamic-group %s to use keys in compartment id %s where target.key.id = ''%s''",
  "Allow dynamic-group %s to read objects in compartment id %s where target.bucket.name = ''%s''",
  "Allow dynamic-group %s to use virtual-network-family in compartment id %s"
]' "$DYNAMIC_GROUP_NAME" "${COMPARTMENT_ID}" "${DB_SECRET_OCID}" "$DYNAMIC_GROUP_NAME" "${COMPARTMENT_ID}" "${VAULT_KEY_OCID}" "$DYNAMIC_GROUP_NAME" "${COMPARTMENT_ID}" "${BUCKET_NAME}" "$DYNAMIC_GROUP_NAME" "${COMPARTMENT_ID}" > /tmp/statements.json

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
# Get an Auth Token from your OCI user profile page (Profile -> Auth Tokens).
echo "<YOUR_AUTH_TOKEN>" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/${OCI_USERNAME} --password-stdin

# 3.2. Configure the Fn CLI Context
fn create context oci-prod --provider oracle
fn use context oci-prod
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
fn update context registry "${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}"

# 3.3. Deploy the function
# The fn CLI will read func.yaml, build the Dockerfile, push to OCIR, and create the function.
fn -v deploy --app ${APP_NAME}

# 3.4. Configure the function with its required environment variables
fn config function ${APP_NAME} ${FUNCTION_NAME} DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} ${FUNCTION_NAME} OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
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
    *   Select your compartment, `${APP_NAME}` application, and `${FUNCTION_NAME}` function.
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

If the end-to-end test fails, follow this battle-tested diagnostic flow.

**Step 1: Check the Function Logs (The Function's Story)**

This is your primary source of truth.
1.  Go to OCI Console -> **Developer Services** -> **Functions**.
2.  Navigate to your application (`${APP_NAME}`) and function (`${FUNCTION_NAME}`).
3.  Click on **Logs**. Set the time filter to the last 5 minutes.

*   **If you see new logs with a `502` or other error code:**
    *   The trigger is working, but the function's code is failing.
    *   Find the JSON log entry with `"level": "CRITICAL"`.
    *   Look at the `"invocation_id"`. Copy it.
    *   Filter the logs by this `invocation_id` to see the entire story of that single execution.
    *   Examine the `"exception"` object in the critical log. The `"type"` and `"message"` will tell you exactly what went wrong (e.g., `OperationalError: connection timed out`, `ValueError: Table does not exist...`, etc.).

*   **If you see NO new logs at all:**
    *   The function is **not being invoked**. The problem is the trigger mechanism or a fundamental networking issue.
    *   **A) Check the Event Rule:** Go to **Events Service -> Rules**. Verify your rule is **Active** and the conditions (Event Type, Service Name, bucketName) are correct.
    *   **B) Check VCN Networking (Most Common Cause of No Logs):** A `504` timeout often manifests as "no logs" because the platform terminates the invocation before the function can log anything.
        1.  **Security List (Firewall):** Ensure the function's subnet has a **Stateful Ingress** rule allowing TCP traffic on port 8080 from the `All ... Services in Oracle Services Network`.
        2.  **Route Table:** Ensure the subnet's route table has a rule directing traffic for `All ... Services in Oracle Services Network` to a **Service Gateway**.
        3.  **DRG Attachment:** If a DRG is attached to the VCN, ensure its attachment configuration uses an **empty DRG Route Table** to disable transit routing, which can cause silent, asymmetric routing failures.
