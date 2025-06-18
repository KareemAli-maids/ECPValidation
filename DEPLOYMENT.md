# Deployment Guide

## Quick Start (Local)

1. **Install Dependencies**:
   ```bash
   pip3 install fastapi uvicorn jinja2 python-multipart
   ```

2. **Run the Server**:
   ```bash
   python3 main.py
   ```

3. **Access the Application**:
   - Web Interface: http://localhost:8000
   - Health Check: http://localhost:8000/health
   - API Documentation: http://localhost:8000/docs

## Deploy to Render

### Option 1: Using render.yaml (Recommended)

1. **Push to GitHub**: Commit all files to your GitHub repository
2. **Connect to Render**: 
   - Go to [render.com](https://render.com)
   - Connect your GitHub repository
   - The `render.yaml` file will automatically configure deployment
3. **Deploy**: Render will automatically build and deploy your application

### Option 2: Manual Configuration

1. **Create New Web Service** on Render
2. **Configuration**:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Environment**: Python 3.11
   - **Plan**: Free tier available

## Deploy to Heroku

1. **Install Heroku CLI**
2. **Create Heroku App**:
   ```bash
   heroku create your-app-name
   ```
3. **Deploy**:
   ```bash
   git push heroku main
   ```

The `Procfile` is already configured for Heroku deployment.

## Environment Variables

The application uses static configuration, but you can set these environment variables if needed:

- `PORT`: Server port (automatically set by hosting platforms)
- `HOST`: Server host (defaults to 0.0.0.0)

## Testing the Deployment

1. **Health Check**: `GET /health`
2. **Web Interface**: `GET /`
3. **API Endpoint**: `POST /api/compare`

Example API test:
```bash
curl -X POST https://your-app.onrender.com/api/compare \
  -H "Content-Type: application/json" \
  -d '{"prompt_name": "Doctors"}'
```

## Troubleshooting

- **Import Errors**: Ensure all dependencies in `requirements.txt` are installed
- **Port Issues**: The app automatically uses the PORT environment variable
- **Memory Issues**: Consider upgrading to a paid plan for larger datasets
- **Timeout Issues**: API calls may take several minutes for large comparisons

## Files Structure

```
├── main.py                 # FastAPI application
├── merge_compare.py        # Comparison logic
├── templates/
│   └── index.html         # Web interface
├── requirements.txt       # Python dependencies
├── render.yaml           # Render deployment config
├── Procfile              # Heroku deployment config
├── runtime.txt           # Python version specification
└── README.md             # Documentation
``` 