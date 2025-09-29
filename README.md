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

**Architectural Decision (PB-20250924-01):The "FDK-less" Architecture** The decision to bypass the official Oracle Python FDK (`fdk`) was made after a critical, unfixable build deadlock was discovered. The `fdk`'s dependency on `httptools==0.4.0` is fundamentally incompatible with the C-API of Python 3.11+. This "FDK-less" approach resolves the build deadlock, unlocks the use of modern Python versions, and improves local testability.


## 2. Prerequisites

Ensure you have the following tools installed and configured:
- **OCI Resources:** A provisioned VCN with a private subnet, a PostgreSQL database, and an OCI Vault with a master key and a secret containing the database credentials.
- **GitHub Repository:** This repository must have the required secrets and variables configured for the CI/CD pipeline (see `deploy.yml`).

---

## 3. Canonical Deployment Method: CI/CD

**Manual deployment of this function is explicitly forbidden.** All deployments **MUST** be performed via the GitHub Actions workflow defined in `.github/workflows/deploy.yml`.

This policy is in place to prevent critical, hard-to-diagnose runtime failures caused by:
1.  **CPU Architecture Mismatch:** Building on an ARM64 machine (like Apple Silicon or OCI Ampere) for the AMD64 OCI Functions platform leads to `exec format error` crashes. The CI/CD pipeline builds on a native AMD64 runner, eliminating this risk.
2.  **Local Environment Drift:** Differences in local Docker, Python, or CLI versions can produce non-viable artifacts. The CI/CD pipeline provides a clean, consistent, and repeatable build environment.

### Deployment Procedure

1.  **Make Code Changes:** Modify the `main.py` or other source files as needed.
2.  **Bump the Version:** **This is a mandatory step.** Before committing, increment the `version` number in `func.yaml`. This forces the OCI Functions platform to pull the new container image. Failure to do so will result in a "no-op" deployment where the old code continues to run.
3.  **Commit and Push:** Push your changes to the `main` branch. The GitHub Actions workflow will automatically build, push, and update the function.---

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


### End-to-End Test

1.  **Trigger the upstream CI/CD pipeline** (`smart-indexing.yml`) in a project repository. This will generate and upload a data payload to the `${BUCKET_NAME}`.
2.  **Monitor the function logs** in the OCI Console. A successful run will show a series of JSON logs, starting with `Function invocation started.` and ending with `Function invocation completed successfully.`.
3.  **Verify the data** in the PostgreSQL database.

### **Proactive Troubleshooting: The Battle-Tested Diagnostic Flow**

If an invocation fails, follow this diagnostic flow. The primary symptom will almost always be a `504 - FunctionInvokeContainerInitTimeout` error in the logs.

**CRITICAL LESSON LEARNED:** 
The `504` error is a generic, misleading symptom. It rarely means a true timeout. It almost always means the **container crashed instantly**, and the platform timed out while waiting for a response that would never come.

**Step 1: Check the Function Logs for ANY Output**

Go to the function's logs in the OCI Console. What do you see for the failed invocation?

*   **Case A: The logs are completely empty.**
    *   **Diagnosis:** Catastrophic container crash *before* the web server can start.
    *   **Cause:** A top-level syntax error in `main.py` (e.g., a typo, bad indentation). The Python interpreter fails the moment it tries to load the module.
    *   **Action:** Carefully review your recent code changes for syntax errors. Test the container locally with `docker run ...` to capture the traceback that OCI is hiding.

*   **Case B: You see the canary (`--- RAG INGESTOR ... DEPLOYED ---`) and Uvicorn startup messages, but NO custom JSON logs.**
    *   **Diagnosis:** The container started successfully but crashed during the processing of the invocation request, before the first line of your handler code.
    *   **Most Likely Cause:** A **data contract mismatch**. The payload received from Object Storage is in a format the function cannot handle.
    *   **Primary Suspect:** **Vector Dimension Mismatch.** Check the `embedding_dim` in the logs of the upstream `smart-indexing` workflow. It **MUST** match the `VECTOR_DIMENSION` constant in this function's `main.py`.
    *   **Action:** Align the `VECTOR_DIMENSION` in `main.py`, bump the version in `func.yaml`, and redeploy.

*   **Case C: You see your custom JSON logs, including a "CRITICAL" level log with an exception.**
    *   **Diagnosis:** The function's core logic is failing. This is the "easiest" problem to solve.
    *   **Action:** Copy the `invocation_id` from the log. Filter the logs by this ID. Examine the `"exception"` object in the critical log. The `"type"` and `"message"` will tell you exactly what went wrong (e.g., `OperationalError: connection timed out`, `ValueError: Table does not exist...`). This points to a problem with the database, networking, or the content of the payload itself.

*   **Case D: You see NO new logs at all after the trigger event.**
    *   **Diagnosis:** The function is **not being invoked**.
    *   **Action:**
        1.  **Check the Event Rule:** Verify it is **Active** and the conditions (bucketName, etc.) are correct.
        2.  **Check VCN Networking:** This is a common cause. Ensure the function's subnet has a **Route Table** with a rule for `All ... Services in Oracle Services Network` pointing to a **Service Gateway**.

Non-`504` error:

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
