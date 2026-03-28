#!/usr/bin/env bash
# deploy/push-ecr.sh — Build, tag, and push the Aura Health image to ECR,
# then generate apprunner-service.json ready for `aws apprunner create-service`.
#
# Usage:
#   export AWS_ACCOUNT_ID=123456789012
#   export AWS_REGION=ap-southeast-1          # must match BEDROCK region
#   export SERVICE_NAME=aura-health           # App Runner service name
#   bash deploy/push-ecr.sh
#
# Prerequisites:
#   - AWS CLI v2 configured (aws configure or IAM role on the build machine)
#   - Docker daemon running
#   - jq installed (brew install jq / apt install jq)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:=ap-southeast-1}"
: "${SERVICE_NAME:=aura-health}"

REPO_NAME="${SERVICE_NAME}"
IMAGE_TAG="latest"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${ECR_REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"
ROLE_NAME="${SERVICE_NAME}-instance-role"
ACCESS_ROLE_NAME="${SERVICE_NAME}-ecr-access-role"

echo "==> Region        : ${AWS_REGION}"
echo "==> Account       : ${AWS_ACCOUNT_ID}"
echo "==> ECR image     : ${IMAGE_URI}"
echo ""

# ── 1. Create ECR repository (idempotent) ─────────────────────────────────────
echo "==> Ensuring ECR repository exists..."
aws ecr describe-repositories \
    --repository-names "${REPO_NAME}" \
    --region "${AWS_REGION}" > /dev/null 2>&1 \
|| aws ecr create-repository \
    --repository-name "${REPO_NAME}" \
    --region "${AWS_REGION}" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256

# ── 2. Authenticate Docker to ECR ─────────────────────────────────────────────
echo "==> Authenticating Docker to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# ── 3. Build & push ───────────────────────────────────────────────────────────
echo "==> Building Docker image (this will take several minutes on first run)..."
cd "$(dirname "$0")/.."
docker build --platform linux/amd64 -t "${REPO_NAME}:${IMAGE_TAG}" .

echo "==> Tagging and pushing to ECR..."
docker tag "${REPO_NAME}:${IMAGE_TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"
echo "    Pushed: ${IMAGE_URI}"

# ── 4. Create / verify IAM instance role (Bedrock access) ────────────────────
echo "==> Setting up IAM instance role for Bedrock access..."
if ! aws iam get-role --role-name "${ROLE_NAME}" > /dev/null 2>&1; then
    aws iam create-role \
        --role-name "${ROLE_NAME}" \
        --assume-role-policy-document file://deploy/iam-instance-trust.json \
        --description "App Runner instance role — allows Bedrock InvokeModel"
    aws iam put-role-policy \
        --role-name "${ROLE_NAME}" \
        --policy-name "BedrockAccess" \
        --policy-document file://deploy/iam-bedrock-policy.json
    echo "    Created role: ${ROLE_NAME}"
else
    echo "    Role already exists: ${ROLE_NAME}"
fi
INSTANCE_ROLE_ARN=$(aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.Arn' --output text)

# ── 5. Create / verify IAM ECR access role (App Runner pulls from ECR) ────────
echo "==> Setting up IAM ECR access role..."
ECR_TRUST=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "build.apprunner.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
)
if ! aws iam get-role --role-name "${ACCESS_ROLE_NAME}" > /dev/null 2>&1; then
    aws iam create-role \
        --role-name "${ACCESS_ROLE_NAME}" \
        --assume-role-policy-document "${ECR_TRUST}" \
        --description "App Runner ECR access role"
    aws iam attach-role-policy \
        --role-name "${ACCESS_ROLE_NAME}" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
    echo "    Created ECR access role: ${ACCESS_ROLE_NAME}"
else
    echo "    ECR access role already exists: ${ACCESS_ROLE_NAME}"
fi
ACCESS_ROLE_ARN=$(aws iam get-role --role-name "${ACCESS_ROLE_NAME}" --query 'Role.Arn' --output text)

# ── 6. Generate apprunner-service.json ────────────────────────────────────────
echo "==> Writing deploy/apprunner-service.json..."
cat > deploy/apprunner-service.json <<EOF
{
  "ServiceName": "${SERVICE_NAME}",
  "SourceConfiguration": {
    "ImageRepository": {
      "ImageIdentifier": "${IMAGE_URI}",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8000",
        "RuntimeEnvironmentVariables": {
          "AWS_DEFAULT_REGION":  "${AWS_REGION}",
          "BEDROCK_MODEL":       "anthropic.claude-haiku-4-5-20251001-v1:0",
          "BEDROCK_PROVIDER":    "anthropic",
          "KB_USE_SEED":         "true",
          "KB_ENABLE_CDC":       "false",
          "KB_ENABLE_PUBMED":    "false",
          "KB_ENABLE_OPENFDA":   "false"
        }
      }
    },
    "AutoDeploymentsEnabled": true,
    "AuthenticationConfiguration": {
      "AccessRoleArn": "${ACCESS_ROLE_ARN}"
    }
  },
  "InstanceConfiguration": {
    "Cpu": "2 vCPU",
    "Memory": "4 GB",
    "InstanceRoleArn": "${INSTANCE_ROLE_ARN}"
  },
  "HealthCheckConfiguration": {
    "Protocol": "HTTP",
    "Path": "/health",
    "Interval": 10,
    "Timeout": 5,
    "HealthyThreshold": 1,
    "UnhealthyThreshold": 5
  },
  "AutoScalingConfigurationArn": null
}
EOF

echo ""
echo "==> Done! Deploy App Runner service with:"
echo ""
echo "    aws apprunner create-service \\"
echo "      --cli-input-json file://deploy/apprunner-service.json \\"
echo "      --region ${AWS_REGION}"
echo ""
echo "IMPORTANT: After creating the service, add secrets via the App Runner console:"
echo "  - ANTHROPIC_API_KEY   (fallback if Bedrock is unavailable)"
echo "  - HF_API_TOKEN        (Med42 second-opinion tier)"
echo "  - NCBI_API_KEY        (optional, higher PubMed rate limits)"
echo ""
echo "Map each secret to an AWS Secrets Manager ARN in the environment variables section."
