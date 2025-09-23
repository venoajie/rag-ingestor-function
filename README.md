# RAG Ingestion Function - Deployment Guide v2.1

This repository contains the source code and deployment instructions for the RAG Ingestion Function, a critical component of the three-tiered RAG ecosystem on Oracle Cloud Infrastructure (OCI). This guide has been updated with battle-tested commands and procedures to ensure a robust and repeatable deployment.

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
3.  **Fn Project CLI:** Installed (preferably built from source).
4.  **jq:** A command-line JSON processor (`sudo dnf install -y jq`).

---

## 3. Step-by-Step Deployment Guide

### Phase 0: Environment Setup

Execute this block first to set up all necessary variables for your shell session. This will prevent errors in subsequent steps.

```bash
# --- Set these variables with your specific OCI details ---
export COMPARTMENT_ID="<YOUR_COMPARTMENT_OCID>"
export SUBNET_ID="<YOUR_SUBNET_ID_FOR_THE_FUNCTION>"
export DB_SECRET_OCID="<YOUR_DB_CONNECTION_STRING_SECRET_OCID>"

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
  --subnet-ids "[\"$SUBNET_ID\"]" \
  --freeform-tags "{\"appName\": \"$APP_NAME\"}"
```

### Phase 2: Configure Permissions & Networking

This is the most critical phase. These policies and rules grant all necessary permissions for the system to operate.

```bash
# 2.1. Create the IAM Dynamic Group for the Function
echo "Creating Dynamic Group..."
oci iam dynamic-group create \
  --name "RAGIngestorFunctionDynamicGroup" \
  --description "Dynamic group for the RAG ingestion function" \
  --matching-rule "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT_ID', resource.freeformTags.appName = '$APP_NAME'}"

# 2.2. Create the Function's Own Permissions Policy
# This allows the function to read secrets/objects and use the network.
printf '[
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to read secret-bundles in compartment id %s where target.secret.id = ''%s''",
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to read objects in compartment id %s where target.bucket.name = ''%s''",
  "Allow dynamic-group RAGIngestorFunctionDynamicGroup to use virtual-network-family in compartment id %s"
]' "${COMPARTMENT_ID}" "${DB_SECRET_OCID}" "${COMPARTMENT_ID}" "${BUCKET_NAME}" "${COMPARTMENT_ID}" > /tmp/statements.json

oci iam policy create \
  --compartment-id "${COMPARTMENT_ID}" \
  --name "RAGIngestorFunctionPermissionsPolicy" \
  --description "Grants RAG Ingestor function permissions to read secrets, objects, and use the VCN." \
  --statements file:///tmp/statements.json

# 2.3. Create the Event Service Invocation Policy
# This allows the OCI Events service to invoke your function.
printf '["Allow service events to use fn-function in compartment id %s",
"Allow service FaaS to use virtual-network-family in compartment id %s"]' "${COMPARTMENT_ID}" > /tmp/statements.json

oci iam policy create \
  --compartment-id "${COMPARTMENT_ID}" \
  --name "PlatformServicesFunctionPolicy" \
  --description "Allows the OCI Events service to invoke functions within this compartment." \
  --statements file:///tmp/statements.json

rm /tmp/statements.json
```

**2.4. Configure VCN Security List for Ingress**

The function's subnet needs a rule to allow incoming traffic from OCI services.
1.  In the OCI Console, navigate to your VCN, then to the **Security List** for your function's subnet.
2.  Click **Add Ingress Rules**.
3.  Create a **Stateful** rule with these values:
    *   **Source Type:** `Service`
    *   **Source Service:** `All <region-key> Services in Oracle Services Network` (e.g., `All FRA Services...`)
    *   **IP Protocol:** `TCP`
    *   **Destination Port Range:** `443`
    *   **Description:** `Allow OCI services to invoke Functions`

### Phase 3: Prepare and Fix the Function Code

```bash
# 3.1. Clone the repository
cd /srv/apps # Or your preferred location
git clone <YOUR_FUNCTION_REPO_URL> rag-ingestor-function
cd rag-ingestor-function

# 3.2. [CRITICAL] Fix file permissions if you used 'sudo git clone'
sudo chown -R $USER:$USER .

# 3.3. [CRITICAL] Apply code fix for namespace discovery
# This makes the namespace lookup robust by using an environment variable.
# Replace the line `namespace = object_storage_client.get_namespace().data` in func.py
# with the following block:
#
#     namespace = os.environ.get("OCI_NAMESPACE")
#     if not namespace:
#         logger.critical("FATAL: OCI_NAMESPACE environment variable is not set.")
#         raise ValueError("OCI_NAMESPACE environment variable is not set.")
#
```

### Phase 4: Deploy and Configure the Function

```bash
# 4.1. Log in to OCI Container Registry (OCIR)
# Get an Auth Token from your OCI user profile page.
echo "<YOUR_AUTH_TOKEN>" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/<your-oci-username> --password-stdin

# 4.2. Configure the Fn CLI Context
fn create context oci-prod --provider oracle
fn use context oci-prod
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
fn update context registry "${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}"
fn list contexts # Verify 'oci-prod' is current and configured

# 4.3. Deploy the function
fn deploy --app ${APP_NAME}

# 4.4. Configure the function with its required environment variables
fn config function ${APP_NAME} rag-ingestor DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} rag-ingestor OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
```

### Phase 5: Create the Event Rule Trigger

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
    *   Select your `RAG-Project` compartment, `rag-ecosystem-app` application, and `rag-ingestor` function.
5.  Click **Create Rule**.

**Deployment is now complete and the system is LIVE.**

---

## 4. Operations and Troubleshooting

### End-to-End Test

1.  **On your database host (`prod` server):** Create a test table.
    ```bash
    docker exec postgres-db psql -U platform_admin -d librarian_db -c "CREATE TABLE IF NOT EXISTS test_collection (id TEXT PRIMARY KEY, content TEXT, metadata JSONB, embedding vector(3));"
    ```
2.  **On your deployment host (`dev` server):** Create and upload a test file.
    ```bash
    echo '{"table_name": "test_collection", "chunks_to_upsert": [{"id": "test-id-123", "document": "def test(): pass", "metadata": {"source": "test.py"}, "embedding": [0.1,0.2,0.3]}]}' | gzip > /tmp/test.json.gz
    oci os object put --bucket-name ${BUCKET_NAME} --file /tmp/test.json.gz --name "manual-test.json.gz"
    ```
3.  **On your database host (`prod` server):** Wait 30 seconds, then verify the data.
    ```bash
    sleep 30
    docker exec postgres-db psql -U librarian_user -d librarian_db -c "SELECT * FROM test_collection;"
    ```

### **Proactive Troubleshooting: The Diagnostic Flow**

If the end-to-end test fails, **do not guess**. Follow this diagnostic flow immediately after uploading a test file.

**Step 1: Check the Function Logs (The Function's Story)**

This is your primary source of truth.
1.  Go to OCI Console -> **Developer Services** -> **Functions**.
2.  Navigate to your application (`rag-ecosystem-app`) and function (`rag-ingestor`).
3.  Click on **Logs**. Set the time filter to the last 5 minutes.

*   **If you see new logs with a `502` or other error code:**
    *   The trigger is working, but the function's code is failing.
    *   Read the error message in the logs. It will contain a Python traceback.
    *   **`BucketNotFound` error:** The `OCI_NAMESPACE` environment variable is wrong. Fix it with `fn config function ...`.
    *   **`Authorization failed` or database errors:** The `DB_SECRET_OCID` is wrong, or the database is unreachable.
    *   **`Timeout` error:** The function cannot reach the database. Check the VCN Security List and Route Tables.

*   **If you see NO new logs at all:**
    *   The function is **not being invoked**. The problem is the trigger mechanism. Proceed to Step 2.

**Step 2: Check the Audit Logs (The Platform's Story)**

If the function isn't being invoked, the Audit logs will tell you why.
1.  Go to OCI Console -> **Observability & Management** -> **Audit**.
2.  Set the time filter to the last 5 minutes.
3.  Search for events in your compartment (e.g., search for `RAG-Project`).
4.  Look for an event with the `type` **`com.oraclecloud.function.invokeFunction`** that has a **`status` of `401` or `404` (Not Authorized)**.

*   **If you find a failed `invokeFunction` event:**
    *   This is an **IAM problem**. The Events service is trying to run the function but is being denied.
    *   Expand the log's JSON. Look at the `data.identity.principalName`. This will tell you the true name of the service trying to make the call (e.g., `faas`).
    *   Verify that your `AllowEventsToInvokeFunctionsPolicy` exists and is correct.

*   **If you DO NOT find a failed `invokeFunction` event:**
    *   This means the Events service isn't even trying. The problem is the **Event Rule** itself.
    *   Go to **Events Service -> Rules**.
    *   Verify your rule is **Active**.
    *   Verify the conditions (Event Type, Service Name, bucketName) are correct. Re-creating the rule via the UI is the most reliable fix.