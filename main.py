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
        
        # Fetch data from both sources
        logger.info("Fetching Notion data...")
        notion_records = []
        if request.page_id:
            try:
                notion_records = gather_notion_data()
            except Exception as e:
                logger.warning(f"Notion fetch failed: {e}")
        
        logger.info("Fetching ERP data...")
        erp_records = []
        if request.prompt_name:
            try:
                erp_records = gather_erp_data()
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
        data_rows = []
        for param in all_params:
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
        
        # Create Google Sheet
        logger.info("Creating Google Sheet...")
        sheet_url = create_shared_google_sheet(data_rows)
        
        elapsed_time = time.time() - start_time
        
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 