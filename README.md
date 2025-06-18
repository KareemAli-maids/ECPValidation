# ERP-Notion Comparison Tool

A FastAPI web application that compares data between ERP systems and Notion databases using AI-powered analysis.

## Features

- **Web Interface**: Modern, responsive UI for easy data comparison
- **ERP Integration**: Fetches function values from ERP API with filtering
- **Notion Integration**: Extracts TECHNICAL_FUNCTION_VALUE blocks from Notion pages
- **AI Comparison**: Uses Claude 3.5 Sonnet for semantic comparison analysis
- **Google Sheets Output**: Automatically creates shared Google Sheets with results

## Local Development

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   python main.py
   ```
   Or with uvicorn:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

3. **Access the Web Interface**:
   Open your browser to `http://localhost:8000`

## Deployment on Render

1. **Connect Repository**: Link your GitHub repository to Render
2. **Auto-Deploy**: The `render.yaml` file will automatically configure the deployment
3. **Environment**: The app will run on Python 3.11 with all dependencies installed

## Usage

1. **Access the Web Interface**: Navigate to your deployed URL
2. **Enter Data Sources**:
   - **Notion Page ID**: Extract from Notion page URL (optional)
   - **Prompt Name**: ERP prompt name to search for (optional)
3. **Start Validation**: Click the "Start Validation" button
4. **View Results**: Access the generated Google Sheet with comparison results

## API Endpoints

- `GET /`: Web interface
- `POST /api/compare`: Run comparison process
- `GET /health`: Health check

## Configuration

The application uses secure environment variables for all credentials:
- ERP authentication token
- Notion database configuration  
- Google service account credentials
- Claude API settings

See `SECURITY.md` for detailed setup instructions.

## Requirements

- Python 3.11+
- FastAPI
- Notion API access
- Google Cloud service account
- Anthropic Claude API access
- ERP system access token 