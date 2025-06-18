# Security Configuration Guide

## 🔐 Environment Variables Setup

All sensitive credentials are now managed through environment variables. **Never commit actual credentials to version control.**

### Required Environment Variables

1. **Create a `.env` file** in your project root:
   ```bash
   cp env.example .env
   ```

2. **Fill in your actual credentials** in the `.env` file:

```env
# ERP Configuration
AUTH_TOKEN=your_actual_erp_token_here
PROMPT_NAME=Doctors

# Notion Configuration  
NOTION_TOKEN=your_actual_notion_token_here
DATABASE_URL=https://www.notion.so/your_actual_database_id

# Anthropic Claude Configuration
ANTHROPIC_API_KEY=your_actual_anthropic_key_here

# Google Sheets Configuration (Option 1: Full JSON)
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"your_project",...}

# Google Sheets Configuration (Option 2: Individual fields)
GOOGLE_PROJECT_ID=your_google_project_id
GOOGLE_PRIVATE_KEY_ID=your_private_key_id
GOOGLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nyour_actual_private_key\n-----END PRIVATE KEY-----"
GOOGLE_CLIENT_EMAIL=your_service_account@your_project.iam.gserviceaccount.com
GOOGLE_CLIENT_ID=your_client_id

# Optional
DOMAIN_TO_SHARE=your_domain.com
```

## 🚀 Deployment Security

### Local Development
- ✅ Use `.env` file (already in `.gitignore`)
- ✅ Never commit `.env` to Git
- ✅ Use `env.example` for documentation

### Render Deployment
1. **Go to your Render service settings**
2. **Add Environment Variables** in the dashboard:
   - `AUTH_TOKEN` = your_erp_token
   - `NOTION_TOKEN` = your_notion_token
   - `DATABASE_URL` = your_notion_database_url
   - `ANTHROPIC_API_KEY` = your_claude_key
   - `GOOGLE_SERVICE_ACCOUNT_JSON` = your_full_google_json
   - `DOMAIN_TO_SHARE` = your_domain

### Heroku Deployment
```bash
heroku config:set AUTH_TOKEN="your_erp_token"
heroku config:set NOTION_TOKEN="your_notion_token"
heroku config:set DATABASE_URL="your_notion_database_url"
heroku config:set ANTHROPIC_API_KEY="your_claude_key"
heroku config:set GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
```

## 🔑 Getting Your Credentials

### 1. ERP Auth Token
- Log into your ERP system
- Open browser developer tools → Network tab
- Make any API request
- Copy the `Authorization: Bearer` token

### 2. Notion Token
- Go to [Notion Integrations](https://www.notion.so/my-integrations)
- Create new integration
- Copy the "Internal Integration Token"
- Share your database with the integration

### 3. Anthropic API Key
- Go to [Anthropic Console](https://console.anthropic.com/)
- Create API key
- Copy the key (starts with `sk-ant-`)

### 4. Google Service Account
- Go to [Google Cloud Console](https://console.cloud.google.com/)
- Create service account
- Download JSON key file
- Enable Google Sheets API and Google Drive API
- Either use full JSON or extract individual fields

## 🛡️ Security Best Practices

### ✅ DO:
- Use environment variables for all secrets
- Keep `.env` in `.gitignore`
- Use different credentials for different environments
- Regularly rotate API keys
- Use minimal permissions for service accounts
- Monitor API usage for unusual activity

### ❌ DON'T:
- Commit credentials to Git
- Share credentials in chat/email
- Use production credentials in development
- Store credentials in code comments
- Use overly permissive API scopes

## 🔍 Verification

Test your configuration:
```bash
# Check environment variables are loaded
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('✅ AUTH_TOKEN loaded:', bool(os.getenv('AUTH_TOKEN')))
print('✅ NOTION_TOKEN loaded:', bool(os.getenv('NOTION_TOKEN')))
print('✅ ANTHROPIC_API_KEY loaded:', bool(os.getenv('ANTHROPIC_API_KEY')))
"
```

## 🚨 If Credentials Are Compromised

1. **Immediately revoke** the compromised credentials
2. **Generate new** credentials
3. **Update** environment variables
4. **Redeploy** your application
5. **Monitor** for unusual activity

## 📋 Migration from Hardcoded Values

The application now automatically loads from environment variables. Your old hardcoded values have been replaced with secure environment variable lookups.

If you see this error:
```
❌ Missing required environment variables: AUTH_TOKEN, NOTION_TOKEN, DATABASE_URL, ANTHROPIC_API_KEY
```

It means you need to set up your `.env` file or deployment environment variables. 