#!/usr/bin/env python3
"""
main.py
~~~~~~~
FastAPI application for ERP-Notion comparison with web interface.
"""

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import asyncio
import logging
from typing import Optional, Dict, Any
import time
from datetime import datetime

# Import our comparison logic
from merge_compare import gather_erp_data, gather_notion_data, compare_with_claude, create_shared_google_sheet
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="ERP-Notion Comparison Tool", version="1.0.0")

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Templates and static files
templates = Jinja2Templates(directory="templates")

class ComparisonRequest(BaseModel):
    page_id: Optional[str] = None
    prompt_name: Optional[str] = None

class ComparisonResponse(BaseModel):
    success: bool
    message: str
    sheet_url: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None

# Global progress tracking
progress_data = {
    "current_step": "",
    "progress_percentage": 0,
    "logs": [],
    "status": "idle"  # idle, running, completed, error
}

def update_progress(step: str, percentage: int, log_message: str = None):
    """Update global progress state"""
    global progress_data
    progress_data["current_step"] = step
    progress_data["progress_percentage"] = max(0, min(100, percentage))
    progress_data["status"] = "running"
    
    if log_message:
        timestamp = datetime.now().strftime("%H:%M:%S")
        progress_data["logs"].append({
            "timestamp": timestamp,
            "message": log_message,
            "type": "info"
        })
    
    logger.info(f"Progress: {step} - {percentage}% - {log_message}")

def reset_progress():
    """Reset progress state"""
    global progress_data
    progress_data = {
        "current_step": "",
        "progress_percentage": 0,
        "logs": [],
        "status": "idle"
    }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main comparison interface."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/compare", response_model=ComparisonResponse)
async def compare_data(request: ComparisonRequest):
    """Run the ERP-Notion comparison process."""
    
    # Validate input
    if not request.page_id and not request.prompt_name:
        raise HTTPException(
            status_code=400, 
            detail="At least one data source must be provided (page_id or prompt_name)"
        )
    
    try:
        logger.info("Starting comparison process...")
        start_time = time.time()
        
        # Update global variables if provided
        if request.prompt_name:
            import merge_compare
            merge_compare.PROMPT_NAME = request.prompt_name
        
        if request.page_id:
            import merge_compare
            # Extract page ID from full URL if needed
            if "notion.so" in request.page_id:
                page_id = request.page_id.split('/')[-1].split('?')[0]
            else:
                page_id = request.page_id
            # Update the DATABASE_URL with new page ID
            merge_compare.DATABASE_URL = f"https://www.notion.so/{page_id}"
        
        # Reset and initialize progress
        reset_progress()
        update_progress("Initializing validation process", 2, "Starting ECP validation...")
        
        # Fetch data from both sources
        logger.info("Fetching Notion data...")
        notion_records = []
        if request.page_id:
            try:
                update_progress("Fetching Notion data", 5, "Connecting to Notion database...")
                notion_records = gather_notion_data()
                update_progress("Fetching Notion data", 65, f"Retrieved {len(notion_records)} Notion records")
            except Exception as e:
                logger.warning(f"Notion fetch failed: {e}")
        
        logger.info("Fetching ERP data...")
        erp_records = []
        if request.prompt_name:
            try:
                update_progress("Fetching ERP data", 70, "Connecting to ERP system...")
                erp_records = gather_erp_data()
                update_progress("Fetching ERP data", 80, f"Retrieved {len(erp_records)} ERP records")
            except Exception as e:
                logger.warning(f"ERP fetch failed: {e}")
        
        if not notion_records and not erp_records:
            return ComparisonResponse(
                success=False,
                message="No data found from either source. Please check your inputs and try again."
            )
        
        # Build lookup dictionaries
        notion_lookup = {rec["parameter"].lower(): rec for rec in notion_records if rec.get("parameter")}
        erp_lookup = {rec["parameter"].lower(): rec for rec in erp_records if rec.get("parameter")}
        
        all_params = sorted(set(notion_lookup) | set(erp_lookup))
        logger.info(f"Total parameters: Notion={len(notion_lookup)}, ERP={len(erp_lookup)}, Combined={len(all_params)}")
        
        # Prepare data rows for comparison
        update_progress("AI Analysis with Claude", 82, f"Analyzing {len(all_params)} parameters with Claude...")
        data_rows = []
        for i, param in enumerate(all_params):
            notion_json = notion_lookup.get(param, {})
            erp_json = erp_lookup.get(param, {})
            
            # Use Claude for comparison only if both sides exist
            if notion_json and erp_json:
                comparison_text = compare_with_claude(notion_json, erp_json)
            elif notion_json and not erp_json:
                comparison_text = "Parameter missing in ERP"
            elif erp_json and not notion_json:
                comparison_text = "Parameter missing in Notion"
            else:
                comparison_text = "No data"
            
            import json
            data_rows.append([
                param,
                json.dumps(notion_json, ensure_ascii=False, indent=2),
                json.dumps(erp_json, ensure_ascii=False, indent=2),
                comparison_text,
            ])
            
            # Update progress during analysis
            if i % 5 == 0:  # Update every 5 parameters
                progress = 82 + (i / len(all_params)) * 8  # 82-90%
                update_progress("AI Analysis with Claude", int(progress), f"Analyzed {i+1}/{len(all_params)} parameters")
        
        update_progress("Generating comparison report", 90, "Analysis complete, preparing report...")
        
        # Create Google Sheet
        logger.info("Creating Google Sheet...")
        update_progress("Creating Google Sheet", 95, "Setting up Google Sheets...")
        sheet_url = create_shared_google_sheet(data_rows)
        update_progress("Creating Google Sheet", 100, "Google Sheet created successfully!")
        
        elapsed_time = time.time() - start_time
        
        # Mark as completed
        progress_data["status"] = "completed"
        
        return ComparisonResponse(
            success=True,
            message=f"Comparison completed successfully in {elapsed_time:.1f} seconds!",
            sheet_url=sheet_url,
            summary={
                "notionRecords": len(notion_records),
                "erpRecords": len(erp_records),
                "totalRows": len(data_rows),
                "processingTime": f"{elapsed_time:.1f}s"
            }
        )
        
    except Exception as e:
        logger.error(f"Comparison failed: {e}")
        return ComparisonResponse(
            success=False,
            message=f"Comparison failed: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/api/progress")
async def get_progress():
    """Get current progress status"""
    return progress_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 