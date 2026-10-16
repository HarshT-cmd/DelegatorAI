# DelegatorAI

DelegatorAI coordinates college project tasks. The foundation and AI milestones
provide a FastAPI service, SQLite persistence, structured reply interpretation,
validated task updates, and an audit trail. The Google Chat milestone adds
authenticated HTTP events and Chat API messaging. The Streamlit dashboard remains
for a later milestone.

## Local setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `LLM_API_KEY` in `.env` to a newly issued NVIDIA API key. Do not commit
`.env`, service-account credentials, or keys. The LLM settings default to
NVIDIA's OpenAI-compatible endpoint and `openai/gpt-oss-20b`; provider settings
can be overridden with `LLM_BASE_URL` and `LLM_MODEL`.

Seed five simulated participants and sample tasks:

```powershell
python seed.py
```

The simulated participants do not have Google Chat user IDs, so they cannot
receive or submit real Chat messages until you map their records to actual
Google user resource names (for example `users/12345678901234567890`).

## Run the backend

```powershell
uvicorn main:app --host 127.0.0.1 --port 8000
```

- Health check: `GET http://127.0.0.1:8000/health`
- Google Chat event handler: `POST /google-chat/events`

The event handler validates Google's bearer ID token against `CHAT_AUDIENCE`,
ignores unknown senders, spaces, or threads, and only processes messages in a
tracked task thread from that task's configured owner. Original replies and
status changes are stored in SQLite. Duplicate Google event IDs are ignored.
Clarifications and acknowledgements are sent as actual Google Chat thread
messages. LLM and Chat API calls require their own valid credentials; tests use
fakes and do not contact either service.

## Configure Google Chat in the existing Google Cloud project

The app currently uses one HTTPS event URL for all configured Chat triggers:

```text
https://<your-public-host>/google-chat/events
```

`http://127.0.0.1:8000/google-chat/events` is only a local address; Google Chat
cannot call it. The final public HTTPS URL depends on where the backend is
hosted or which HTTPS tunnel you choose. Once selected, use that exact URL both
as the Chat app's HTTP endpoint and as `CHAT_AUDIENCE` (including the path and
trailing-slash choice).

1. In Google Cloud Console, select the existing **DelegatorAI** project and
   enable the **Google Chat API**.
2. Open the Google Chat API configuration. Configure the app connection to use
   the **HTTP endpoint URL** above, and select the matching HTTP endpoint URL
   as the authentication audience. Add the `MESSAGE`, `ADDED_TO_SPACE`, and
   `REMOVED_FROM_SPACE` event triggers to this same endpoint. Lifecycle events
   receive an empty acknowledgement; task replies are handled on `MESSAGE`.
3. Configure a service account for the Chat app's app authentication. Grant the
   Chat app the `https://www.googleapis.com/auth/chat.bot` scope. For local
   development, download its JSON key to a protected location outside this
   repository. Do not commit that file. In production, prefer an attached
   service identity over a downloaded key.
4. Add the Google Chat app to each project space. Set `CHAT_PROJECT_SPACES` in
   `.env` to a JSON map from task `project_id` to the Google Chat space resource
   name, for example:

   ```text
   CHAT_PROJECT_SPACES={"college-app":"spaces/AAAA..."}
   ```

5. Set these values in `.env`:
   - `CHAT_AUDIENCE`: the exact public HTTPS URL configured in the Chat app.
   - `CHAT_SERVICE_ACCOUNT_FILE`: path to the service account JSON file for
     outbound Chat API calls.
   - `CHAT_EXPECTED_SERVICE_ACCOUNT_EMAIL`: optionally pin the service account
     email expected in Google's verified bearer token.
   - `CHAT_PROJECT_SPACES`: JSON project-to-space mapping.

   The backend verifies the Google-signed OIDC ID token's signature, issuer, and
   audience. Do not disable that validation or replace it with a shared secret.
6. Update each real participant record's `google_user_id` to the `users/...`
   name Google provides in Chat events. It must match the actual task owner.
   Seeded sample identities are not connected to real users.
7. In the Google Chat API configuration, make the app available to the intended
   test users, then send a real message in a task thread to test the full loop.

### Cloud Run demo deployment

This repository includes a Dockerfile for Cloud Run. The demo configuration
keeps SQLite at `/tmp/delegatorai.db`: this is intentionally disposable data,
not durable storage. Cloud Run can replace an instance or scale out, so tasks,
messages, and audit history can disappear or diverge. Do not use this SQLite
deployment for real participant coordination where updates must be retained.

Install and authenticate the Google Cloud CLI, then set the project and region
in PowerShell:

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
$region = "YOUR_REGION"
$project = "YOUR_PROJECT_ID"
```

Enable the required APIs and create a runtime service account:

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
  secretmanager.googleapis.com chat.googleapis.com --project $project
gcloud iam service-accounts create delegatorai-chat `
  --project $project --display-name "DelegatorAI Chat API client"
```

Create a Secret Manager secret named `delegatorai-llm-key` in the Cloud Console,
add a newly rotated NVIDIA key as its secret value, and grant the runtime
service account `roles/secretmanager.secretAccessor` on that secret. Do not
pass keys on the command line or commit them. Deploy from the repository root:

```powershell
$runtimeServiceAccount = "delegatorai-chat@$project.iam.gserviceaccount.com"
gcloud run deploy delegatorai `
  --source . `
  --project $project `
  --region $region `
  --service-account $runtimeServiceAccount `
  --allow-unauthenticated `
  --max 1 `
  --set-env-vars "DATABASE_PATH=/tmp/delegatorai.db,LLM_BASE_URL=https://integrate.api.nvidia.com/v1,LLM_MODEL=openai/gpt-oss-20b" `
  --set-secrets "LLM_API_KEY=delegatorai-llm-key:latest"
```

`--allow-unauthenticated` lets Google Chat reach the HTTPS service. The app
still rejects requests unless their bearer token is Google-signed and has the
configured audience. Retrieve the actual Cloud Run service URL:

```powershell
$serviceUrl = gcloud run services describe delegatorai `
  --project $project --region $region --format "value(status.url)"
$chatEndpoint = "$serviceUrl/google-chat/events"
Write-Output $chatEndpoint
```

Set `CHAT_AUDIENCE` to the exact `$chatEndpoint` value on the Cloud Run service,
then select **Use a common HTTP endpoint URL for all triggers** in Google Chat
API configuration and enter that same URL. If the **App home** trigger has its
own URL field, use the same URL there; the endpoint responds to `APP_HOME`,
`MESSAGE`, and lifecycle events. If the service URL changes, update both the
Cloud Run `CHAT_AUDIENCE` setting and the Google Chat trigger URLs to match
exactly.

In the Chat API configuration, also set the generated Chat service account
email as `CHAT_EXPECTED_SERVICE_ACCOUNT_EMAIL` when available. It has the form
`service-PROJECT_NUMBER@gcp-sa-gsuiteaddons.iam.gserviceaccount.com`. Set
`CHAT_PROJECT_SPACES` to a JSON map of each task project's ID to its actual
space resource name, and populate each real task owner's `google_user_id` with
the user's `users/...` resource name. The runtime service account supplies
Application Default Credentials for outbound Chat API calls; no downloaded
service-account key file is required on Cloud Run.

### HTTPS for local development

Use an HTTPS tunnel or deploy the backend to an HTTPS host. Keep the tunnel
running while Google Chat is configured and testing. Put the tunnel's HTTPS URL
plus `/google-chat/events` in both the Chat app configuration and `CHAT_AUDIENCE`.
Do not publish the SQLite file, `.env`, or service-account JSON through the
tunnel. The Cloud Run demo deployment uses ephemeral SQLite and one maximum
instance; it is not a durable production deployment.

## Verify

```powershell
python -m pytest -q
```

The tests verify persistence, status rules, reply matching, ownership checks,
event idempotency, and the HTTP endpoint's authentication guard. They do not
prove that a Google Cloud project, Chat app, API key, or service account is
configured, and they do not send a live Chat message.
