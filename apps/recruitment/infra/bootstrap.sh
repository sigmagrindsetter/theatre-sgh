#!/usr/bin/env bash
# One-time GCP bootstrap for the recruitment review panel (REVIEW_PANEL_SPEC.md §6.2).
#
# Creates everything GitHub Actions needs to build, deploy and operate the Cloud Run
# service without any key file: APIs, Artifact Registry repo, runtime + deployer service
# accounts, Workload Identity Federation trust for this repository on branch main,
# and empty Secret Manager secrets.
#
# Idempotent: every step checks before it creates. Needs project-owner rights.
# Intended to be run once, in Cloud Shell from a fresh clone of the repo:
#   bash apps/recruitment/infra/bootstrap.sh            # apply
#   bash apps/recruitment/infra/bootstrap.sh --dry-run  # only print what would change
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-theatre-sgh}"
REGION="${REGION:-europe-west1}"
GITHUB_REPO="${GITHUB_REPO:-sigmagrindsetter/theatre-sgh}"
DEPLOY_REF="${DEPLOY_REF:-refs/heads/main}"
AR_REPO="panel"
RUNTIME_SA_NAME="panel-runtime"
DEPLOYER_SA_NAME="panel-deployer"
POOL_ID="github"
PROVIDER_ID="theatre-sgh"
SECRETS=(NOTION_API_TOKEN NOTION_OAUTH_CLIENT_ID NOTION_OAUTH_CLIENT_SECRET SESSION_SECRET INGEST_TOKEN)

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

G=(gcloud --project "$PROJECT_ID" --quiet)
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

log() { printf '\n== %s\n' "$*"; }
change() {  # print the mutating command; execute unless --dry-run
  printf '+ %s\n' "$*"
  (( DRY_RUN )) || "$@"
}
exists() { "${G[@]}" "$@" >/dev/null 2>&1; }

log "Project $PROJECT_ID, region $REGION, repo $GITHUB_REPO ($DEPLOY_REF)"
PROJECT_NUMBER="$("${G[@]}" projects describe "$PROJECT_ID" --format='value(projectNumber)')"
echo "project number: $PROJECT_NUMBER"

log "APIs"
change "${G[@]}" services enable \
  run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudscheduler.googleapis.com cloudresourcemanager.googleapis.com

log "Artifact Registry repo $AR_REPO ($REGION)"
if exists artifacts repositories describe "$AR_REPO" --location="$REGION"; then
  echo "exists"
else
  change "${G[@]}" artifacts repositories create "$AR_REPO" --location="$REGION" \
    --repository-format=docker --description="Recruitment panel images"
fi
POLICY_FILE="$(mktemp)"
cat >"$POLICY_FILE" <<'JSON'
[
  {"name": "keep-last-5", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-older-than-30d", "action": {"type": "Delete"}, "condition": {"olderThan": "2592000s"}}
]
JSON
change "${G[@]}" artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" \
  --policy="$POLICY_FILE" --no-dry-run
rm -f "$POLICY_FILE"

log "Service accounts"
for sa in "$RUNTIME_SA_NAME:Recruitment panel runtime" "$DEPLOYER_SA_NAME:Recruitment panel deployer (GitHub Actions)"; do
  name="${sa%%:*}"; display="${sa#*:}"
  if exists iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com"; then
    echo "$name exists"
  else
    change "${G[@]}" iam service-accounts create "$name" --display-name="$display"
  fi
done
# Cloud Scheduler needs its service agent to mint OIDC tokens as the runtime SA.
change "${G[@]}" beta services identity create --service=cloudscheduler.googleapis.com

log "Secrets (empty; versions are added by the panel-secrets workflow)"
for s in "${SECRETS[@]}"; do
  if exists secrets describe "$s"; then
    echo "$s exists"
  else
    change "${G[@]}" secrets create "$s" --replication-policy=automatic
  fi
  change "${G[@]}" secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor
  change "${G[@]}" secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${DEPLOYER_SA}" --role=roles/secretmanager.secretVersionManager
done

log "Deployer permissions"
change "${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/run.admin
change "${G[@]}" projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/cloudscheduler.admin
change "${G[@]}" artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/artifactregistry.writer
change "${G[@]}" iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser

log "Workload Identity Federation (GitHub Actions → $DEPLOYER_SA)"
if exists iam workload-identity-pools describe "$POOL_ID" --location=global; then
  echo "pool exists"
else
  change "${G[@]}" iam workload-identity-pools create "$POOL_ID" --location=global \
    --display-name="GitHub Actions"
fi
if exists iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --location=global --workload-identity-pool="$POOL_ID"; then
  echo "provider exists"
else
  change "${G[@]}" iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --location=global --workload-identity-pool="$POOL_ID" \
    --display-name="$GITHUB_REPO" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}' && assertion.ref == '${DEPLOY_REF}'"
fi
POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
change "${G[@]}" iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPO}"

log "Done. Put these into GitHub repository variables:"
echo "GCP_WIF_PROVIDER=${POOL_NAME}/providers/${PROVIDER_ID}"
echo "GCP_DEPLOYER_SA=${DEPLOYER_SA}"
echo "Expected service URL: https://panel-rekrutacja-${PROJECT_NUMBER}.${REGION}.run.app"
