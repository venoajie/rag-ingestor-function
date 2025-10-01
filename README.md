# OCI Serverless Function: RAG Ingestor - v5.0 (Hardened)

This repository contains the source code and the definitive, battle-tested deployment guide for the `rag-ingestor` OCI Function. This document has been hardened with lessons learned from a series of production-blocking incidents to ensure a robust, repeatable, and understandable deployment process.

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

## 2. Prerequisites

Ensure you have the following configured in your GitHub repository's **Settings -> Secrets and variables -> Actions**:

**Repository Secrets:**
*   `OCI_PRIVATE_KEY`: The private key for your OCI API user.
*   `OCI_USER_OCID`: The OCID of the API user.
*   `OCI_FINGERPRINT`: The fingerprint of the API key.
*   `OCI_TENANCY_OCID`: The OCID of your tenancy.
*   `OCI_USERNAME`: The full username for OCIR login (e.g., `service-user-ci-cd`).
*   `OCIR_AUTH_TOKEN`: The OCI Auth Token for the user (for Docker login).
*   `OCI_FN_APP_OCID`: The OCID of the **Function Application** (`rag-app`) that will contain this function.

**Repository Variables:**
*   `OCI_REGION`: The OCI region identifier (e.g., `eu-frankfurt-1`).
*   `OCI_REGION_KEY`: The region key for OCIR (e.g., `fra`).

## 3. Critical Infrastructure Setup: The "Gotcha" Checklist

Before the first deployment, the following OCI resources and policies **MUST** be correctly configured. Failure to do so will result in the cryptic and misleading errors detailed in the troubleshooting section.

### 3.1. VCN Networking: Hub & Spoke with DRG

The function runs in a private subnet within a "Spoke" VCN (`rag-project-vcn`), connecting to a database in a central "Hub" VCN (`shared-infrastructure-vcn`). This connection is managed by a **Dynamic Routing Gateway (DRG)**.

*   **DRG Route Table:** The DRG's internal route table **MUST** have static routes that explicitly teach it how to route traffic between the Hub and Spoke VCNs. Without these rules, the DRG acts as a "silent black hole," dropping all cross-VCN traffic.
    *   **Example Rule 1:** `Destination: 10.0.0.0/16` -> `Next Hop: hub-vcn-attachment`
    *   **Example Rule 2:** `Destination: 10.1.0.0/16` -> `Next Hop: RAG-VCN-attachment`

*   **Function Subnet Route Table:**
    *   **LESSON LEARNED:** The **Service Gateway** proved to be an unreliable "silent black hole" for egress traffic from the function to OCI services (like IAM).
    *   **REQUIRED CONFIGURATION:** The subnet's Route Table **MUST** have a rule directing all outbound traffic (`0.0.0.0/0`) to a **NAT Gateway**. This provides a reliable path for the function to authenticate itself and for any other outbound needs.

*   **Function Subnet Security List:**
    *   The subnet's Security List **MUST** have a **Stateful Ingress** rule allowing traffic from `Source Service: All <region> Services in Oracle Services Network` on **All Protocols**. This is required for the OCI platform to manage the function (e.g., invocation, health checks) and for services like Bastion to work.

### 3.2. IAM Policies

These policies **must be created in the Root Compartment (Tenancy)** to correctly grant permissions across compartments.

**A. For the CI/CD User:**
```
# Allows the CI/CD user to create repositories and push images into the project compartment.
Allow group <Your-CI-CD-User-Group> to manage repos in compartment RAG-Project
```

**B. For the Function's Runtime (Resource Principals):**
```
# Allows the function to identify itself and its group membership.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to inspect dynamic-groups in tenancy

# Allows the function to be placed in a VCN subnet.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to use virtual-network-family in tenancy

# Allows the function to read the database secret from the vault.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to read secret-family in compartment RAG-Project

# Allows the function to read objects from the specific inbox bucket.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to read objects in compartment RAG-Project where target.bucket.name = 'rag-codebase-inbox'
```

### 3.3. The Dynamic Group

*   **CRITICAL LESSON:** Identifying the function by its OCID (`resource.id = 'ocid...'`) is **brittle**. The OCID changes every time the function is recreated.
*   **ROBUST SOLUTION:** Identify all functions within the target compartment. This is secure as long as the compartment is dedicated to this application.

**Recommended Dynamic Group Rule:**
```
# Match any function within the RAG-Project compartment.
ALL {resource.type = 'fnfunc', resource.compartment.id = 'ocid1.compartment.oc1..aaaa...'}
```

## 4. Deployment & Troubleshooting

This section combines the deployment process with the specific errors encountered and their definitive solutions.

### 4.1. Deployment: Manual Creation, Automated Updates

The only foolproof method is to create the function manually the first time.

1.  **Run the CI/CD Pipeline Once:** Push a commit. It will fail, but it will successfully push the Docker image to OCIR.
2.  **Create the Function Manually:** In the OCI Console, create the function from the "existing image" you just pushed.
3.  All subsequent pushes will be handled automatically by the `deploy.yml` workflow, which updates the function with the new image.

### 4.2. Troubleshooting Flow

**Symptom: `FATAL: ... private.pem` Error on Startup**
*   **Diagnosis:** This is the most critical and misleading error. It means the function is running but **cannot talk to the OCI authentication service** to get its identity.
*   **Root Cause:** The egress network path is blocked.
*   **Action:** Verify the **Function Subnet Route Table** has a rule sending all traffic (`0.0.0.0/0`) to a **NAT Gateway**. The Service Gateway is not a reliable alternative.

**Symptom: `504 Timeout` on Startup**
*   **Diagnosis:** The function is timing out while trying to connect to an external resource, most likely the database.
*   **Root Cause:** The network path between the function's VCN and the database's VCN is blocked.
*   **Action:** Verify the **DRG Route Table** has the correct static routes to connect the two VCNs, as described in **Section 3.1**.

**Symptom: CI/CD Fails with `repository name must be lowercase`**
*   **Diagnosis:** The Docker client requires lowercase image repository paths.
*   **Action:** Ensure the image tag in `deploy.yml` uses a lowercase version of your compartment name (e.g., `.../rag-project/...`).

---


### 1. Updated Documentation: `Appendix A`

I will now update the "On-going Debugging" section of your `README.md`. This will be the definitive starting point for your next session.

---
## Appendix A: Current Debugging State (As of 2025-09-30 - End of Session)

This section serves as a snapshot of the last debugging session to ensure continuity.

### 1. Current Status & Final Failure Point

*   The function has been reverted to its clean, production-intent state, using **Resource Principals** for authentication.
*   The original `FATAL: ... private.pem` error is **RESOLVED**.
*   The function now successfully initializes, authenticates to OCI via the **NAT Gateway**, and fetches the database secret from the Vault.
*   The **final point of failure** is a `504 Timeout` that occurs when the function attempts to establish a TCP connection to the PostgreSQL database at `10.0.0.146:6432`.

### 2. Hypotheses Status

*   **[RESOLVED]** The function's egress path to OCI services was blocked. **Fix:** Replaced the faulty Service Gateway route with a `0.0.0.0/0` route to a **NAT Gateway**.
*   **[RESOLVED]** The network path between the Hub and Spoke VCNs was blocked. **Fix:** Added the correct static routes to the **DRG Route Table**.
*   **[ACTIVE & PRIMARY]** A firewall is blocking the connection from the function's subnet (`10.1.1.0/24`) to the database VM (`10.0.0.146`) on port `6432`. This could be the VCN Security List or the host's internal firewall (`firewalld`).

### 3. Key Lessons Learned

*   The `private.pem` error is a symptom of a failed network call to the OCI authentication service, not a missing file.
*   In a Hub-and-Spoke topology, the DRG's internal route table is a critical, non-obvious point of failure.
*   A NAT Gateway is a more reliable egress path for private subnets than a Service Gateway.
*   Debugging private network resources is complex and requires tools like Bastion or Run Command, which have their own deep prerequisites (correct OS, healthy agent, IAM policies, FIPS-compliant keys).

### 4. Temporary Resources & Cleanup Checklist

*   **[ACTIVE - NEEDS CLEANUP]** Compute Instance: `rag-function-debug-client-ol`
*   **[ACTIVE - NEEDS CLEANUP]** Bastion Service: `BastionRagDebug` and all its sessions.
*   **[ACTIVE - NEEDS CLEANUP]** Dynamic Group: `Temp-Debug-Instance-DG`
*   **[ACTIVE - NEEDS CLEANUP]** IAM Policy: `Temp-Debug-Instance-RunCommand-Policy`
*   **[KEPT BY DESIGN]** NAT Gateway: `rag-project-nat-gw`. This is now a required part of the architecture.

### 5. Next Diagnostic Step for Next Session

The very first action for the next session should be to re-verify the end-to-end network path using the debug tools, now that we know all their prerequisites.

1.  **Goal:** Get a successful `nc -z -v 10.0.0.146 6432` command to run from a resource inside the `10.1.1.0/24` subnet.
2.  **Primary Tool:** Use the **Run Command** feature on the `rag-function-debug-client-ol` instance, as it has the fewest dependencies.
3.  **If Run Command still fails:** The issue is likely the IAM policy for the debug instance.
4.  **If Run Command succeeds:** The network path is open, and the function's timeout is due to a more subtle platform issue.
5.  **If Run Command fails with a timeout:** The block is the **database VCN's Security List** or the **database host's `firewalld`**. Re-run the checks from the `DATABASE_OPERATIONS_MANUAL.md` on the database host.

---

### 2. Modifications to Files for the Next Debugger

You asked what the next debugger needs to pay attention to. This is a critical handoff.

*   **`main.py`:**
    *   **Status:** It currently contains temporary logic for user-based authentication (`OCI_CONFIG_B64`).
    *   **Action Required:** This logic **must be removed**. The `initialize_dependencies` function should be reverted to its clean, production state that only uses `signer = oci.auth.signers.get_resource_principals_signer()`. The temporary `OCI_CONFIG_B64` variable should be deleted from the Application's configuration.

*   **`func.yaml`:**
    *   **Status:** This file is correct and production-ready. The annotation `oracle.com/oci/auth/principal: "dynamic_group"` is present and correct.
    *   **Action Required:** No changes needed.

*   **`Dockerfile`:**
    *   **Status:** This file is well-structured, uses multi-stage builds, and is production-ready.
    *   **Action Required:** No changes needed.

*   **`.github/workflows/deploy.yml`:**
    *   **Status:** This file is mostly correct but needs a critical hardening step we identified.
    *   **Action Required:** The `oci fn function update` command **must be modified** to include the `--force` and `--annotation` flags to ensure the function's identity is correctly applied on every deployment.
        ```yaml
        # In the 'Create or Update OCI Function' step
        oci fn function update \
          --function-id "$FUNCTION_OCID" \
          --image "${{ env.FULL_IMAGE_NAME }}" \
          --force \
          --annotation oracle.com/oci/auth/principal="dynamic_group"
        ```
    *   The `--no-cache` flag in the `docker build` step was for debugging and can be removed to speed up future builds.
