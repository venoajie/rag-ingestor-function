**Your Role:** You are a project lead overseeing a critical RAG-based application. Your core serverless ingestion pipeline on OCI is blocked by a persistent, platform-level bug (`FunctionInvokeSubnetNotAvailable`). You have made the strategic decision to migrate the serverless component of this pipeline to AWS to unblock your project. Your goal is to generate a complete, detailed, and actionable migration plan.

**Your Prompt:**

"Hello,

I require a comprehensive, step-by-step migration plan to move a serverless ingestion function from Oracle Cloud Infrastructure (OCI) to AWS Lambda. My project is currently blocked by a persistent OCI platform issue (SR attached for context), making this migration a top priority.

The goal is to create a production-grade, secure, and cost-effective solution on AWS that integrates seamlessly with my existing CI/CD pipeline and my database, which will remain on OCI.

Below is the complete context of my current architecture, including the function code, CI/CD workflow, and key infrastructure details.

**Current Architecture Overview:**

1.  **CI/CD (Producer):** A GitHub Actions workflow (`smart-indexing.yml`) checks out a project's source code, runs an indexer tool to generate a `new_chunks.json.gz` data artifact, and uploads this artifact to an OCI Object Storage "inbox" bucket.
2.  **Event Trigger (OCI):** An OCI Events Rule monitors the inbox bucket for `Object - Create` events.
3.  **Serverless Function (The Component to be Migrated):** An OCI Function (`rag-ingestor`) is triggered by the event. Its job is to:
    *   Download the `.json.gz` file from Object Storage.
    *   Fetch a database connection string from OCI Vault.
    *   Connect to a PostgreSQL database (with `pgvector`) running on a separate OCI Compute VM.
    *   Perform a transactional upsert/delete operation on the data.
4.  **Database (Consumer, Staying on OCI):** A PostgreSQL 17 database runs within Docker on an OCI Compute VM (`prod`) in a dedicated "Hub" VCN.

**I need you to generate a complete migration plan that covers the following five areas:**

**1. AWS Environment Preparation:**
    *   Detail the foundational AWS resources that need to be created. This must include:
        *   An S3 bucket to replace the OCI "inbox" bucket.
        *   An IAM Role for the Lambda function, specifying the exact permissions (JSON policy documents) required to read from the S3 bucket and fetch secrets from AWS Secrets Manager.
        *   An AWS Secrets Manager secret to store the PostgreSQL connection string.

**2. Multi-Cloud Networking (AWS to OCI):**
    *   Provide a detailed, step-by-step guide for establishing a secure network connection from a new AWS VPC to my existing OCI "Hub" VCN (`shared-infrastructure-vcn`, CIDR `10.0.0.0/16`).
    *   My OCI database VM is located at private IP `10.0.0.146` in a **private subnet**.
    *   Recommend and detail the setup for the most appropriate connection method (e.g., Site-to-Site VPN between an AWS Transit Gateway and an OCI DRG).
    *   Specify the exact routing table and security group/list rules required on both the AWS and OCI sides to allow the Lambda function to connect to the database on port `6432`.

**3. Lambda Function Adaptation:**
    *   Provide the refactored Python code (`func.py`) for the Lambda function. This should replace OCI SDK calls (`oci`) with their AWS SDK (`boto3`) equivalents for fetching the object from S3 and the secret from Secrets Manager.
    *   Provide the updated `Dockerfile` and `requirements.txt` needed to package the function as an AWS Lambda container image.
    *   Detail the Lambda function configuration, including memory size, timeout, VPC/subnet assignment, and environment variables.

**4. CI/CD Workflow (`smart-indexing.yml`) Adaptation:**
    *   Provide the modified GitHub Actions workflow file.
    *   The changes should replace the OCI CLI authentication and `oci os object put` commands with their AWS CLI equivalents (`aws s3 cp`).
    *   This includes setting up AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) as new GitHub repository secrets.
    *   Ensure the logic for uploading the data artifact to the new S3 "inbox" bucket and the state file to its S3 location is correct.

**5. Cost Analysis:**
    *   Provide a detailed cost estimate for the new AWS solution, assuming a workload of approximately 100 invocations per day.
    *   Analyze the costs for AWS Lambda, S3, Secrets Manager, and the multi-cloud networking components.
    *   Confirm whether this workload will operate within the AWS Free Tier and clearly state any components that will incur a cost.

Please structure your response clearly into these five sections. This plan will serve as our definitive guide for the migration.

---
**[Attached for Context: `func.py`, `Dockerfile`, `smart-indexing.yml`]**"
