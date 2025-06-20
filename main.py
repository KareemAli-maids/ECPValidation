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
import threading

# Import our comparison logic
from merge_compare import gather_erp_data, gather_notion_data, compare_with_claude, create_shared_google_sheet, split_large_text
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
    "status": "idle"  # idle, running, completed, error, cancelled
}

# Global cancellation flag – set by /api/stop or browser unload
cancel_event = threading.Event()

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
    cancel_event.clear()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main comparison interface."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/compare", response_model=ComparisonResponse)
async def compare_data(request: ComparisonRequest):
    """Run the ERP-Notion comparison process *without* blocking the event loop.

    The heavy synchronous work is executed in a background thread via
    ``asyncio.to_thread`` so that the main asyncio event-loop remains free to
    serve the ``/api/progress`` polling requests coming from the browser.  This
    guarantees that the UI can receive real-time updates while the comparison
    is still running.
    """
    
    # ---------------------------------------------------------------
    # 1. Validate request payload early – run on event-loop thread
    # ---------------------------------------------------------------
    if not request.page_id and not request.prompt_name:
        raise HTTPException(
            status_code=400, 
            detail="At least one data source must be provided (page_id or prompt_name)",
        )

    # ---------------------------------------------------------------
    # 2. Define the **blocking** worker function
    # ---------------------------------------------------------------
    def _perform_comparison(page_id: str | None, prompt_name: str | None) -> ComparisonResponse:
        """Synchronous worker executed in a thread."""

        logger.info("Starting comparison process…")
        start_time = time.time()
        
        # Dynamically update merge_compare globals (safe inside the worker thread)
        import merge_compare as mc
        if prompt_name:
            mc.PROMPT_NAME = prompt_name
        if page_id:
            # Extract raw ID if a full Notion URL is provided
            nid = (
                page_id.split("/")[-1].split("?")[0] if "notion.so" in page_id else page_id
            )
            mc.DATABASE_URL = f"https://www.notion.so/{nid}"
        
        # Reset + initialise progress
        reset_progress()
        update_progress("Initializing validation process", 2, "Starting ECP validation…")
        # Clear any previous cancel signal
        cancel_event.clear()

        # Register callback so helper functions can push granular updates
        mc.set_progress_callback(update_progress)
        mc.set_cancel_event(cancel_event)

        # -------------------------------------------------------
        # 2.a Fetch Notion data
        # -------------------------------------------------------
        notion_records: list[dict] = []
        if page_id:
            try:
                update_progress("Fetching Notion data", 5, "Connecting to Notion database…")
                notion_records = gather_notion_data()
                update_progress(
                    "Fetching Notion data",
                    65,
                    f"Retrieved {len(notion_records)} Notion records",
                )
            except Exception as exc:
                logger.warning("Notion fetch failed: %s", exc)
        
        # -------------------------------------------------------
        # 2.b Fetch ERP data
        # -------------------------------------------------------
        erp_records: list[dict] = []
        if prompt_name:
            try:
                update_progress("Fetching ERP data", 70, "Connecting to ERP system…")
                erp_records = gather_erp_data()
                update_progress(
                    "Fetching ERP data",
                    80,
                    f"Retrieved {len(erp_records)} ERP records",
                )
            except Exception as exc:
                logger.warning("ERP fetch failed: %s", exc)
        
        if not notion_records and not erp_records:
            return ComparisonResponse(
                success=False,
                message="No data found from either source. Please check your inputs and try again.",
            )
        
        # -------------------------------------------------------
        # 2.c Run Claude analysis & build comparison rows
        # -------------------------------------------------------
        notion_lookup = {r["parameter"].lower(): r for r in notion_records if r.get("parameter")}
        erp_lookup = {r["parameter"].lower(): r for r in erp_records if r.get("parameter")}
        
        # Separate parameters into different categories
        both_params = sorted(set(notion_lookup) & set(erp_lookup))  # Parameters in both systems
        notion_only_params = sorted(set(notion_lookup) - set(erp_lookup))  # Only in Notion
        erp_only_params = sorted(set(erp_lookup) - set(notion_lookup))  # Only in ERP
        
        logger.info(
            "Parameter distribution: Notion=%d, ERP=%d, Both=%d, Notion-only=%d, ERP-only=%d",
            len(notion_lookup),
            len(erp_lookup),
            len(both_params),
            len(notion_only_params),
            len(erp_only_params),
        )

        update_progress(
            "AI Analysis with Claude", 82, f"Analyzing {len(both_params)} parameters with Claude…"
        )

        import json
        data_rows: list[list[str]] = []
        
        # First, process parameters that exist in BOTH systems (with Claude comparison)
        for idx, param in enumerate(both_params):
            n_json = notion_lookup[param]
            e_json = erp_lookup[param]
            
            # Use Claude for comparison since both exist (with original complete data)
            cmp_text = compare_with_claude(n_json, e_json)

            # Convert to compact JSON strings to save space
            notion_json_str = json.dumps(n_json, ensure_ascii=False, separators=(',', ':'))
            erp_json_str = json.dumps(e_json, ensure_ascii=False, separators=(',', ':'))
            
            # Split large JSON strings to avoid 50k character limit
            notion_chunks = split_large_text(notion_json_str)
            erp_chunks = split_large_text(erp_json_str)
            
            # Determine how many rows we need (max of notion and erp chunks)
            max_chunks = max(len(notion_chunks), len(erp_chunks))
            
            # Create rows - first row has parameter name and comparison, subsequent rows are continuations
            for i in range(max_chunks):
                if i == 0:
                    # First row: include parameter name and comparison
                    row = [
                        param,
                        notion_chunks[i] if i < len(notion_chunks) else "",
                        erp_chunks[i] if i < len(erp_chunks) else "",
                        cmp_text
                    ]
                else:
                    # Continuation rows: empty parameter name and comparison
                    row = [
                        f"  └─ {param} (cont.)",  # Indented continuation indicator
                        notion_chunks[i] if i < len(notion_chunks) else "",
                        erp_chunks[i] if i < len(erp_chunks) else "",
                        ""  # Empty comparison for continuation rows
                    ]
                
                data_rows.append(row)

            if idx % 5 == 0:
                pct = 82 + int((idx / len(both_params)) * 6)  # 82-88%
                update_progress(
                    "AI Analysis with Claude",
                    pct,
                    f"Analyzed {idx + 1}/{len(both_params)} parameters",
                )

            if cancel_event.is_set():
                update_progress("Cancelled", progress_data.get("progress_percentage", 0), "Validation cancelled by user")
                progress_data["status"] = "cancelled"
                return ComparisonResponse(success=False, message="Validation cancelled by user")
        
        # Store the count of both_params rows for later organization
        both_params_row_count = len(data_rows)
        
        # Then, add parameters that exist only in ERP (no Claude comparison)
        update_progress("Adding ERP-only parameters", 88, f"Adding {len(erp_only_params)} ERP-only parameters…")
        for param in erp_only_params:
            e_json = erp_lookup[param]
            
            # Convert to compact JSON and split if needed
            erp_json_str = json.dumps(e_json, ensure_ascii=False, separators=(',', ':'))
            erp_chunks = split_large_text(erp_json_str)
            
            # Create rows for ERP-only parameter
            for i, chunk in enumerate(erp_chunks):
                if i == 0:
                    # First row: include parameter name and comparison
                    row = [
                        param,
                        "",  # Empty JSON for Notion
                        chunk,
                        "Parameter missing in Notion"
                    ]
                else:
                    # Continuation rows
                    row = [
                        f"  └─ {param} (cont.)",
                        "",  # Empty JSON for Notion
                        chunk,
                        ""  # Empty comparison for continuation rows
                    ]
                
                data_rows.append(row)
        
        # Store the count of ERP-only rows for later organization
        erp_only_row_count = len(data_rows) - both_params_row_count
        
        # Finally, add parameters that exist only in Notion (no Claude comparison)
        update_progress("Adding Notion-only parameters", 89, f"Adding {len(notion_only_params)} Notion-only parameters…")
        for param in notion_only_params:
            n_json = notion_lookup[param]
            
            # Convert to compact JSON and split if needed
            notion_json_str = json.dumps(n_json, ensure_ascii=False, separators=(',', ':'))
            notion_chunks = split_large_text(notion_json_str)
            
            # Create rows for Notion-only parameter
            for i, chunk in enumerate(notion_chunks):
                if i == 0:
                    # First row: include parameter name and comparison
                    row = [
                        param,
                        chunk,
                        "",  # Empty JSON for ERP
                        "Parameter missing in ERP"
                    ]
                else:
                    # Continuation rows
                    row = [
                        f"  └─ {param} (cont.)",
                        chunk,
                        "",  # Empty JSON for ERP
                        ""  # Empty comparison for continuation rows
                    ]
                
                data_rows.append(row)

        # -------------------------------------------------------
        # 2.d Create Google Sheet with organized sections
        # -------------------------------------------------------
        update_progress("Generating comparison report", 90, "Analysis complete, preparing report…")
        update_progress("Creating Google Sheet", 95, "Setting up Google Sheets…")

        # Organize data with section headers
        organized_data = []
        section_headers = []  # Track which rows are section headers for formatting
        
        # Section 1: AI Comparisons (parameters in both systems) - NO HEADER
        if both_params:
            for i in range(both_params_row_count):
                organized_data.append(data_rows[i])
        
        # Section 2: ERP-only parameters
        if erp_only_params:
            organized_data.append(["", "", "", ""])  # Empty row for spacing
            header_row_index = len(organized_data)
            organized_data.append(["=== ERP-ONLY PARAMETERS ===", "", "", ""])
            section_headers.append(header_row_index)  # Track this row for red highlighting
            for i in range(both_params_row_count, both_params_row_count + erp_only_row_count):
                organized_data.append(data_rows[i])
        
        # Section 3: Notion-only parameters
        if notion_only_params:
            organized_data.append(["", "", "", ""])  # Empty row for spacing
            header_row_index = len(organized_data)
            organized_data.append(["=== NOTION-ONLY PARAMETERS ===", "", "", ""])
            section_headers.append(header_row_index)  # Track this row for red highlighting
            for i in range(both_params_row_count + erp_only_row_count, len(data_rows)):
                organized_data.append(data_rows[i])

        sheet_url = create_shared_google_sheet(organized_data, section_headers)
        update_progress("Creating Google Sheet", 100, "Google Sheet created successfully!")
        
        elapsed = time.time() - start_time
        progress_data["status"] = "completed"
        
        # Before returning, check cancellation once more
        if cancel_event.is_set():
            progress_data["status"] = "cancelled"
            return ComparisonResponse(success=False, message="Validation cancelled by user")

        return ComparisonResponse(
            success=True,
            message=f"Comparison completed successfully in {elapsed:.1f} seconds!",
            sheet_url=sheet_url,
            summary={
                "notionRecords": len(notion_records),
                "erpRecords": len(erp_records),
                "totalComparisons": len(both_params),  # Only parameters that exist in both systems
                "totalRows": len(data_rows),  # Total rows in the sheet
                "processingTime": f"{elapsed:.1f}s",
            },
        )
        
    # ---------------------------------------------------------------
    # 3. Off-load work to a background thread & await result
    # ---------------------------------------------------------------
    try:
        response: ComparisonResponse = await asyncio.to_thread(
            _perform_comparison, request.page_id, request.prompt_name
        )
        return response
    except Exception as exc:
        logger.exception("Comparison failed: %s", exc)
        return ComparisonResponse(success=False, message=f"Comparison failed: {exc}")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/api/progress")
async def get_progress():
    """Get current progress status"""
    logger.debug(f"Progress requested: {progress_data}")
    return progress_data

@app.post("/api/reset-progress")
async def reset_progress_endpoint():
    """Reset progress status"""
    reset_progress()
    return {"message": "Progress reset successfully"}

@app.post("/api/test-progress")
async def test_progress():
    """Test progress updates"""
    reset_progress()
    for i in range(0, 101, 10):
        update_progress("Testing progress", i, f"Test step {i}")
        await asyncio.sleep(0.1)
    progress_data["status"] = "completed"
    return {"message": "Progress test completed"}

@app.post("/api/stop")
async def stop_validation():
    """Signal cancellation of the running validation job."""
    if progress_data.get("status") != "running":
        return {"message": "No validation in progress"}

    cancel_event.set()
    progress_data["status"] = "cancelled"
    update_progress("Cancelling", progress_data.get("progress_percentage", 0), "User requested cancellation")
    return {"message": "Cancellation signal sent"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 