# Deploying to Azure App Service

Deployment is handled by **Azure Deployment Center**, which is already connected to
this GitHub repo. Pushing to the deploy branch (usually `main`) triggers the
Azure-generated GitHub Actions workflow (`.github/workflows/main_<appname>.yml`),
which builds the Python app and deploys it. **No manual deploy step is needed.**

## Required one-time Azure configuration

The code deploys automatically, but the app will not run correctly until these are
set on the Web App in the Azure Portal.

### 1. Application settings (Configuration → Application settings)

The three `JIRA_*` secrets are deliberately NOT in the repo, so they must be set here:

| Name | Value |
|------|-------|
| `JIRA_BASE_URL` | `https://lifedata.atlassian.net` |
| `JIRA_EMAIL` | your Jira account email |
| `JIRA_API_TOKEN` | read-only Jira API token |
| `JIRA_PROJECTS` | `LIFEDATAV2` (optional) |
| `JIRA_WINDOW_DAYS` | `14` (optional) |
| `JIRA_CACHE_TTL` | `300` seconds (optional) — how long fetched Jira data is cached in memory. Raise it to make pages snappier at the cost of freshness; set `0` to disable caching. |

### 2. Startup Command (Configuration → General settings → Startup Command)

Azure usually auto-detects the Flask app, but set this explicitly to be safe:

```
gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 2 app:app
```

The WSGI entrypoint is `app:app` (the `app` object in `app.py`).

## How a deploy happens

1. Push code to the deploy branch.
2. Azure's workflow runs (visible under the repo's **Actions** tab).
3. The new version goes live on the Web App URL.

> Never commit `.env`; secrets live only in Azure Application settings.
