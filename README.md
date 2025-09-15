# Serverless RAG Ingestion Function on OCI

This repository contains the source code and deployment instructions for the RAG Ingestion Function, a critical component of the three-tiered RAG ecosystem on Oracle Cloud Infrastructure (OCI).

## 1. Overview

This is a serverless, event-driven OCI Function responsible for securely and efficiently loading indexed codebase data into a central PostgreSQL database. It is designed to be the "Ingestor" component, triggered automatically when the "Producer" (a CI/CD workflow) uploads a new data file to an OCI Object Storage bucket.

### Architecture Flow
```
+------------------+      +----------------------+      +--------------------+      +------------------+
|                  |      |                      |      |                    |      |                  |
|  GitHub Actions  |----->| OCI Object Storage   |----->| OCI Function       |----->|   PostgreSQL     |
|  (CI/CD Producer)|      | (rag-codebase-inbox) |      | (This Function)    |      |   Database       |
|                  |      |                      |      |                    |      |                  |
+------------------+      +----------------------+      +--------------------+      +------------------+
```

## 2. Prerequisites

Before you begin, ensure you have the following tools installed and configured:

1.  **OCI CLI:** Installed and configured with a user profile (`~/.oci/config`).
2.  **Docker:** Installed, running, and able to execute commands.
3.  **Fn Project CLI:** The Fn Project CLI is required to interact with the OCI Functions service. The most reliable way to ensure compatibility is to build it from source. This guide requires Go 1.21 or newer.

    *   **Installation Steps:**

        1.  **Install Go (Golang):**
            *   Check if Go is installed by running `go version`. If it's 1.21+ you can skip this step.
            *   To install Go for your architecture (example for ARM64):
                ```bash
                # Download the latest Go binary for your architecture
                wget https://go.dev/dl/go1.22.0.linux-arm64.tar.gz

                # Remove any old Go installation and extract the new one
                sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf go1.22.0.linux-arm64.tar.gz

                # Add Go to your PATH in your shell configuration file (~/.bashrc, ~/.zshrc, etc.)
                echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
                source ~/.bashrc
                ```

        2.  **Build and Install the Fn CLI:**
            ```bash
            # Clone the official Fn Project CLI repository
            git clone https://github.com/fnproject/cli.git fn-cli

            # Navigate into the directory and build the binary
            cd fn-cli
            make build

            # Move the compiled 'fn' binary to a location in your system's PATH
            sudo mv fn /usr/local/bin/
            ```

    *   **To Verify:** Run `fn --version`. You should see the version number of the CLI you just built.

4.  **jq:** A command-line JSON processor, used for scripting. (`sudo yum install jq` or `sudo apt-get install jq`).
5.  **OCI Resources:**
    *   A running PostgreSQL database with the `pgvector` extension.
    *   The database connection string stored as a Secret in an OCI Vault.
    *   An OCI user with permissions to manage Functions, IAM, Object Storage, and Events.

## 3. Step-by-Step Deployment Guide

This guide provides the exact, battle-tested commands to deploy the function from a development instance (like an OCI Compute instance).

### Step 1: Provision OCI Infrastructure

These commands use the OCI CLI to create all necessary cloud resources.

```bash
# --- Set environment variables for your tenancy ---
export COMPARTMENT_ID="<YOUR_COMPARTMENT_OCID>"
export SUBNET_ID="<YOUR_SUBNET_ID>"
export DB_SECRET_OCID="<YOUR_DB_SECRET_OCID>"
export APP_NAME="rag-ecosystem-app"
export BUCKET_NAME="rag-codebase-inbox"

# 1.1. Create the Object Storage "Inbox" Bucket
echo "Creating Object Storage bucket: $BUCKET_NAME..."
oci os bucket create \
  --compartment-id $COMPARTMENT_ID \
  --name $BUCKET_NAME \
  --emit-object-events true

# 1.2. Create the IAM Dynamic Group for the Function
echo "Creating Dynamic Group..."
oci iam dynamic-group create \
  --name "RAGIngestorFunctionDynamicGroup" \
  --description "Dynamic group for the RAG ingestion function" \
  --matching-rule "ALL {resource.type = 'fnfunc', resource.compartment.id = '$COMPARTMENT_ID', resource.freeformTags.appName = '$APP_NAME'}"

# 1.3. Create the IAM Policy
echo "Creating IAM Policy..."
oci iam policy create \
  --compartment-id $COMPARTMENT_ID \
  --name "RAGIngestorFunctionPolicy" \
  --description "Grants RAG Ingestor function necessary permissions" \
  --statements "[
    \"Allow dynamic-group RAGIngestorFunctionDynamicGroup to read objects in compartment id $COMPARTMENT_ID where target.bucket.name = '$BUCKET_NAME'\",
    \"Allow dynamic-group RAGIngestorFunctionDynamicGroup to read secret-bundles in compartment id $COMPARTMENT_ID where target.secret.id = '$DB_SECRET_OCID'\",
    \"Allow dynamic-group RAGIngestorFunctionDynamicGroup to use vnics in compartment id $COMPARTMENT_ID\",
    \"Allow dynamic-group RAGIngestorFunctionDynamicGroup to use subnets in compartment id $COMPARTMENT_ID\",
    \"Allow dynamic-group RAGIngestorFunctionDynamicGroup to use network-security-groups in compartment id $COMPARTMENT_ID\"
  ]"

# 1.4. Create the OCI Function Application
echo "Creating Functions Application: $APP_NAME..."
oci fn application create \
  --compartment-id $COMPARTMENT_ID \
  --display-name $APP_NAME \
  --subnet-ids "[\"$SUBNET_ID\"]" \
  --freeform-tags "{\"appName\": \"$APP_NAME\"}"
```

### Step 2: Configure Local Tooling

This is the most critical configuration step. It ensures your local CLI tools can communicate correctly with OCI.

1.  **Log in to OCI Container Registry (OCIR):**
    ```bash
    # Get your OCI Auth Token from your user profile in the OCI Console.
    # This is more secure than passing the password directly on the command line.
    echo "<YOUR_AUTH_TOKEN>" | docker login <region-key>.ocir.io -u <tenancy-namespace>/<your-oci-username> --password-stdin
    echo "wD***V]" | docker login fra.ocir.io -u fr**es/vxx.xx@xx.com --password-stdin
    ```

2.  **Create and Configure the Fn CLI Context:**
    The `fn` CLI needs a context with the `oracle` provider to authenticate correctly.

    ```bash
    # 2.1. Create a new context with the 'oracle' provider
    fn create context oci-prod --provider oracle

    # 2.2. Switch to the new context
    fn use context oci-prod

    # 2.3. Configure the context with your specific OCI details
    # Replace placeholders accordingly.
    fn update context oracle.compartment-id <YOUR_COMPARTMENT_OCID>
    fn update context api-url https://functions.<your-region>.oci.oraclecloud.com
    fn update context registry <your-region-key>.ocir.io/<your_tenancy_namespace>

    # 2.4. Verify the context is fully configured
    echo "✅ Verifying Fn context..."
    fn list contexts
    # Ensure the line for 'oci-prod' has the PROVIDER, API URL, and REGISTRY fields populated.
    ```

### Step 3: Build, Push, and Deploy the Function

Navigate into this repository's directory before running these commands.

```bash
# 1. Build the function's container image.
# This reads func.yaml and builds the image with the correct tag.
fn build

# 2. Push the image to the OCI Container Registry.
fn push

# 3. Deploy the function to your application in OCI.
fn deploy --app rag-ecosystem-app
```

### Step 4: Post-Deployment Configuration

The function is deployed but needs its database credentials and the event trigger to become operational.

1.  **Configure the Function with the Database Secret:**
    ```bash
    fn config function rag-ecosystem-app rag-ingestor DB_SECRET_OCID "<YOUR_DB_SECRET_OCID>"
    ```

2.  **Create the Event Rule (The Trigger):**
    This process uses a temporary file to avoid shell quoting issues, which is the most robust method.

    ```bash
    # 2.1. Get the function's OCID and strip the extra quotes from the CLI's output.
    FUNCTION_ID=$(fn inspect function rag-ecosystem-app rag-ingestor id | tr -d '"')
    echo "Captured Function ID: $FUNCTION_ID"

    # 2.2. Create a perfectly formatted JSON file for the --actions parameter.
    printf '{"actions":[{"actionType":"FAAS","functionId":"%s","isEnabled":true}]}' "$FUNCTION_ID" > actions.json
    echo "✅ Created actions.json file:"
    cat actions.json

    # 2.3. Create the event rule by referencing the JSON file.
    oci events rule create \
      --display-name "TriggerRAGIngestorOnNewCodebaseFile" \
      --is-enabled true \
      --compartment-id "<YOUR_COMPARTMENT_OCID>" \
      --condition '{
        "eventType": "com.oraclecloud.objectstorage.createobject",
        "data": {
          "additionalDetails": {
            "bucketName": "rag-codebase-inbox"
          },
          "resourceName.suffix": ".json.gz"
        }
      }' \
      --actions file://actions.json

    # 2.4. Clean up the temporary file.
    rm actions.json
    ```

**Deployment is now complete and the system is LIVE.**

## 4. Operations and Maintenance

### Testing the Pipeline

To perform a full end-to-end test:
1.  Create a test file: `echo '[{"source": "test.py", "chunk_text": "test", "embedding": [0.1]}]' | gzip > test.json.gz`
2.  Upload it to the bucket: `oci os object put --bucket-name rag-codebase-inbox --file test.json.gz --name "manual-test.json.gz"`
3.  Monitor the logs in the OCI Console and verify the data appears in your database.

### Monitoring

All function logs are available in the OCI Console under **Observability & Management -> Logging -> Logs**.

### Updating the Function

To deploy a new version of the function:
1.  Make your code changes in `func.py`.
2.  Update the version number in `func.yaml` (e.g., to `0.0.2`).
3.  Run `fn deploy --app rag-ecosystem-app`. This single command will automatically handle the build, push, and update process.
