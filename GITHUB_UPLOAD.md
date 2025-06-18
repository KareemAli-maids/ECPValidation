# GitHub Upload Instructions

## 📁 Files Ready for GitHub

This folder contains all the files that are **safe to upload** to GitHub. All sensitive credentials have been removed and replaced with environment variables.

### ✅ Safe Files Included:

- **`main.py`** - FastAPI application (no hardcoded credentials)
- **`merge_compare.py`** - Comparison logic (uses environment variables)
- **`templates/index.html`** - Web interface template
- **`requirements.txt`** - Python dependencies
- **`render.yaml`** - Render deployment configuration
- **`Procfile`** - Heroku deployment configuration
- **`runtime.txt`** - Python version specification
- **`.gitignore`** - Prevents sensitive files from being committed
- **`env.example`** - Template for environment variables (no real credentials)
- **`README.md`** - Project documentation
- **`SECURITY.md`** - Security setup guide
- **`DEPLOYMENT.md`** - Deployment instructions

### ❌ Files NOT Included (Sensitive):

- **`.env`** - Contains your actual credentials (stays local)
- **`setup_env.py`** - Temporary setup script (deleted)
- **`*.xlsx`** - Output files with data
- **`*.csv`** - Output files with data

## 🚀 Upload Steps

### Option 1: GitHub Web Interface

1. **Create New Repository** on GitHub
2. **Upload Files**: Drag and drop all files from this `github` folder
3. **Commit**: Add commit message like "Initial commit - ERP-Notion comparison tool"

### Option 2: Git Command Line

```bash
# Navigate to the github folder
cd github

# Initialize git repository
git init

# Add all files
git add .

# Commit files
git commit -m "Initial commit - ERP-Notion comparison tool"

# Add remote repository (replace with your GitHub repo URL)
git remote add origin https://github.com/yourusername/your-repo-name.git

# Push to GitHub
git push -u origin main
```

### Option 3: GitHub CLI

```bash
# Navigate to the github folder
cd github

# Initialize git and create GitHub repo
gh repo create your-repo-name --public --source=. --remote=origin --push
```

## 🔒 Security Verification

Before uploading, verify no sensitive data is included:

```bash
# Check for potential credentials in files
grep -r "eyJ" github/  # Should return nothing (no JWT tokens)
grep -r "sk-ant" github/  # Should return nothing (no Anthropic keys)
grep -r "ntn_" github/  # Should return nothing (no Notion tokens)
```

## 🌐 After Upload

1. **Set Environment Variables** in your deployment platform:
   - Render: Service Settings → Environment Variables
   - Heroku: `heroku config:set VARIABLE_NAME=value`
   - Vercel: Project Settings → Environment Variables

2. **Share Repository** safely - no credentials are exposed

3. **Clone and Setup** for others:
   ```bash
   git clone https://github.com/yourusername/your-repo-name.git
   cd your-repo-name
   cp env.example .env
   # Edit .env with actual credentials
   pip install -r requirements.txt
   python3 main.py
   ```

## 📋 Repository Description

Use this description for your GitHub repository:

```
ERP-Notion Comparison Tool

A FastAPI web application that compares data between ERP systems and Notion databases using AI-powered analysis. Features secure credential management, modern web interface, and automated Google Sheets reporting.

Tech Stack: FastAPI, Python, Notion API, Anthropic Claude, Google Sheets API
```

## 🏷️ Suggested Tags

- `fastapi`
- `notion-api`
- `anthropic`
- `google-sheets`
- `erp-integration`
- `data-comparison`
- `ai-analysis`
- `web-app`

## ⚠️ Important Notes

- **Never commit your `.env` file** - it contains real credentials
- **Always use environment variables** for sensitive data
- **Test deployment** with environment variables before going live
- **Keep your local `.env` file** for development 