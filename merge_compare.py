#!/usr/bin/env python3
"""
merge_compare.py
~~~~~~~~~~~~~~~~
Pipeline to:
1. Fetch GPTPromptParameter configs from ERP (copied from erpfetch.py)
2. Fetch TECHNICAL_FUNCTION_VALUE configs from Notion (copied from kareemdatabasetest.py)
3. Compare each parameter using Anthropic Claude with a strict semantic-difference prompt
   adapted from the original Google Apps Script (Code.gs).
4. Create a shared Google Sheet with comparison results that anyone in the domain can access:
      parameter | Notion JSON | ERP JSON | Claude comparison result

Install dependencies first:
  pip install requests python-dotenv notion-client tqdm gspread google-auth
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import textwrap
import threading
import time
import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from tqdm import tqdm
from notion_client import Client
from notion_client.errors import APIResponseError, RequestTimeoutError
import gspread
from google.oauth2.service_account import Credentials

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG / ENVIRONMENT VARIABLES
# ---------------------------------------------------------------------------

AUTH_TOKEN = os.getenv("AUTH_TOKEN")
PROMPT_NAME = os.getenv("PROMPT_NAME", "Doctors")  # Default fallback
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Validate required environment variables
required_vars = {
    "AUTH_TOKEN": AUTH_TOKEN,
    "NOTION_TOKEN": NOTION_TOKEN,
    "DATABASE_URL": DATABASE_URL,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
    print("Please check your .env file or environment variable configuration.")
    sys.exit(1)

# Output workbook path
XLSX_OUT = Path("comparison_results.xlsx")

# Google Sheets Configuration
def get_google_credentials():
    """Get Google service account credentials from environment variables."""
    # Try to get full JSON first
    google_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if google_json:
        try:
            return json.loads(google_json)
        except json.JSONDecodeError:
            print("❌ Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON")
            sys.exit(1)
    
    # Fallback to individual environment variables
    return {
        "type": "service_account",
        "project_id": os.getenv("GOOGLE_PROJECT_ID"),
        "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
        "private_key": os.getenv("GOOGLE_PRIVATE_KEY", "").replace('\\n', '\n'),
        "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{os.getenv('GOOGLE_CLIENT_EMAIL', '').replace('@', '%40')}",
        "universe_domain": "googleapis.com"
    }

GOOGLE_SHEETS_CREDENTIALS = get_google_credentials()

# Domain for sharing (anyone with this domain can access)
DOMAIN_TO_SHARE = os.getenv("DOMAIN_TO_SHARE", "maids.cc")

# ERP Configuration
PAGE_SIZE = 100
API_ROOT = "https://erpbackendpro.maids.cc/chatai/gptpromptparameter"

# Initialize global notion client variable for the helper functions
notion = None

# Global progress callback - can be set by main.py
progress_callback = None
cancel_event = None

def set_progress_callback(callback):
    """Set the progress callback function to be called during data gathering."""
    global progress_callback
    progress_callback = callback

def set_cancel_event(event):
    """Register a threading.Event that signals cancellation."""
    global cancel_event
    cancel_event = event

# Set up logging for debugging
log = logging.getLogger(__name__)
log.setLevel(logging.WARNING)

# Disable verbose HTTP logging from notion client and urllib3
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Claude comparison prompt (trimmed from Code.gs – keep exactly same semantics)
# ---------------------------------------------------------------------------

COMPARISON_PROMPT = (
    "# JSON Configuration Semantic Comparison Prompt\n\n"
    "You are an expert JSON analyst specializing in TECHNICAL_FUNCTION_VALUE configuration comparison. "
    "Your task is to identify ONLY meaningful semantic differences that would cause functional failures while being extremely strict about ignoring equivalent variations.\n\n"
    "## CRITICAL FOCUS AREAS\n\n"
    "ANALYZE ONLY:\n"
    "- Missing conditional branches that change business logic outcomes\n"
    "- Additional conditional branches that change business logic outcomes\n  "
    "- Different comparison values that alter system behavior\n"
    "- Missing conditions that would cause incorrect routing or processing\n\n"
    "IGNORE:\n"
    "- Bracket types, escape characters, JSON formatting / ordering\n"
    "- Prompt names, variable naming, condition order when logically equivalent\n"
    "- Empty or trivial 'else' conditions in ERP JSON (conditions with empty values, just dots '.', or whitespace)\n"
    "- Additional 'else' conditions in ERP JSON that contain no meaningful content\n\n"
    "## SPECIAL HANDLING FOR ERP 'ELSE' CONDITIONS:\n"
    "The ERP system automatically includes 'else' conditions even when they are empty or contain only trivial content like '.' or whitespace. "
    "Do NOT flag these as differences unless they contain actual meaningful logic or values that would change system behavior.\n\n"
    "RULES:\n"
    "1. If no functional issues exist, reply exactly: 'No significant functional differences found.'\n"
    "2. Otherwise, list each issue as a bullet starting with * .\n\n"
    "## COMPARISON TASK\n"
    "Compare these two JSON configurations and identify ONLY semantic differences that affect functionality:\n\n"
    "NOTION JSON (Reference):\n```json\n{{NOTION_JSON}}\n```\n\n"
    "ERP JSON (Target):\n```json\n{{ERP_JSON}}\n```\n"
)

# ---------------------------------------------------------------------------
# Helper – call Anthropic Claude
# ---------------------------------------------------------------------------

def compare_with_claude(notion_json: Dict[str, Any] | List[Any], erp_json: Dict[str, Any] | List[Any]) -> str:
    """Return Claude comparison output (stripped)."""

    prompt = (
        COMPARISON_PROMPT.replace("{{NOTION_JSON}}", json.dumps(notion_json, ensure_ascii=False, indent=2))
        .replace("{{ERP_JSON}}", json.dumps(erp_json, ensure_ascii=False, indent=2))
    )

    payload = {
        "model": "claude-3-sonnet-20240229",
        "max_tokens": 1024,
        "temperature": 0.1,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "content-type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            logging.warning("Claude API error %s: %s", resp.status_code, resp.text[:200])
            return f"API Error {resp.status_code}: {resp.text[:100]}"
        data = resp.json()
        # Claude v1 format: top-level 'content' list with dicts containing 'text'
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text", "").strip()
        return "[Unexpected Claude response]"
    except Exception as e:
        logging.error("Claude comparison failed: %s", e)
        return f"Error calling Claude: {e}"

# ---------------------------------------------------------------------------
# ERP FETCH HELPERS (copied from erpfetch.py)
# ---------------------------------------------------------------------------

BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.8",
    "authorization": f"Bearer {AUTH_TOKEN}",
    "cookie": (
        "deviceIdProduction=1741032443123; "
        "mfaCodeProduction=399936; "
        "isERPAuth=KareemAli; "
        "user=%7B%22loginName%22%3A%22KareemAli%22%7D; "
        f"authTokenProduction={AUTH_TOKEN}"
    ),
    "origin": "https://erp.maids.cc",
    "referer": "https://erp.maids.cc/",
    "priority": "u=1, i",
    "sec-ch-ua": '"Chromium";v="137", "Brave";v="137", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "sec-gpc": "1",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
    "cache-control": "no-cache, no-store, max-age=0, must-revalidate",
    "pragma": "no-cache",
}

def get_search_filter_header_value(prompt_name: str) -> str:
    """Generate search filter header value for dynamic prompt name filtering with creation date."""
    return json.dumps({
        "and": True,
        "left": {
            "field": "G.creationDate",
            "operation": ">",
            "value": "2025-05-01 12:17:57",
            "fieldType": "timestamp",
            "required": False
        },
        "right": {
            "field": "P.name",
            "operation": "Contains",
            "value": prompt_name,
            "fieldType": "string",
            "required": False
        }
    })


PAGE_HEADERS = {
    **BASE_HEADERS,
    "pagecode": "chatai__input_parameters_for_prompts",
    "searchfilter": get_search_filter_header_value(PROMPT_NAME),
}

DETAIL_HEADERS = {
    **BASE_HEADERS,
    "pagecode": "chatai__input_parameters_for_prompts_add_edit",
}

# Expression helpers
FIELD_RENAMES = {"maidType": "Client_Type"}

def _normalise_op(op: str) -> str:
    op = op.upper()
    return {"=": "==", "IS NULL": "IS NULL", "IS NOT NULL": "IS NOT NULL"}.get(op, op)

def _expr_to_string(node: Dict[str, Any]) -> str:
    if node.get("leaf", False) or not ("left" in node and "right" in node):
        field = node.get("fieldName", "")
        if field and field.startswith("$context."):
            field = field[len("$context."):]
        field = FIELD_RENAMES.get(field, field)
        op = _normalise_op(node.get("operation", ""))
        val = node.get("value")
        return f"{field} {op}" if op.startswith("IS") else f"{field} {op} {val}"
    left = _expr_to_string(node["left"])
    right = _expr_to_string(node["right"])
    logic = node.get("logicalOperator", "").upper()
    return f"( {left} {logic} {right} )"

def convert_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Transform a GPTPromptParameter record into the simplified schema."""
    identifier = raw.get("name", "")
    parameter = raw.get("name", "")

    logic: List[Dict[str, str]] = []
    conditions = []
    
    evaluation_type = raw.get("evaluationType", "")
    if evaluation_type == "ERP_CONDITION":
        conditions = raw.get("gptPromptParamConditions", [])
    elif evaluation_type == "API":
        api_data = raw.get("gptPromptParamApi", {})
        conditions = api_data.get("gptConditions", [])
    
    conditions.sort(key=lambda c: c.get("priority", 0))
    
    for cond in conditions:
        expr_tree = cond.get("expression") or json.loads(cond.get("tree", "{}"))
        logic.append({
            "condition": _expr_to_string(expr_tree),
            "value": cond.get("value", "").strip(),
        })
    
    default_value = raw.get("defaultValue", "")
    if default_value and default_value.strip():
        logic.append({
            "condition": "else",
            "value": default_value.strip()
        })

    return {
        "identifier": identifier,
        "parameter": parameter,
        "conditionalLogic": logic,
    }

def fetch_ids() -> List[int]:
    """Fetch all GPTPromptParameter IDs, filtering out CONTEXT evaluation types."""
    ids: List[int] = []
    page = 0
    
    while True:
        params = {"page": page, "size": PAGE_SIZE, "sort": "creationDate,DESC", "search": ""}
        headers = {
            **PAGE_HEADERS,
            "searchfilter": get_search_filter_header_value(PROMPT_NAME)
        }
        
        try:
            resp = requests.get(f"{API_ROOT}/page/", headers=headers, params=params, timeout=30)
            logging.info("/page/ %s → %s", page, resp.status_code)
            
            if resp.status_code in (401, 403):
                raise ValueError("ERP Auth token expired. Please update your .env file.")
            
            resp.raise_for_status()
            chunk = resp.json().get("content", [])
            
            if not chunk:
                break
            
            filtered_chunk = [item for item in chunk if item.get("evaluationType") != "CONTEXT"]
            logging.info("Page %s: %s total records, %s after filtering out CONTEXT types", 
                        page, len(chunk), len(filtered_chunk))
            
            ids.extend(item["id"] for item in filtered_chunk)
            
            if len(chunk) < PAGE_SIZE:
                break
            page += 1
            
        except Exception as err:
            logging.error("Error fetching page %s: %s", page, err)
            if "ERP Auth token expired" in str(err):
                raise
            break
    
    logging.info("Discovered %d IDs", len(ids))
    return ids

def fetch_one(ident: int) -> Dict[str, Any]:
    """Fetch a single GPTPromptParameter record by ID with retry/back-off."""
    max_attempts = 3
    backoff = 1
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(f"{API_ROOT}/{ident}", headers=DETAIL_HEADERS, timeout=30)
            logging.info("GET %s → %s (attempt %d)", ident, resp.status_code, attempt)
    
            # Auth failures should abort immediately
            if resp.status_code in (401, 403):
                raise ValueError("ERP Auth token expired. Please update your .env file.")
    
            if resp.status_code == 200:
                return resp.json()

            # For transient 5xx errors – retry
            if 500 <= resp.status_code < 600:
                raise requests.HTTPError(f"ERP 5xx error: {resp.status_code}")

            # Unexpected non-200, non-5xx – log and break
            preview = textwrap.shorten(resp.text, width=120, placeholder=" …")
            logging.warning("Non-200 body preview (id=%s): %s", ident, preview)
            break
        except Exception as exc:
            logging.warning("fetch_one id=%s failed on attempt %d/%d: %s", ident, attempt, max_attempts, exc)
            if attempt == max_attempts:
                raise
            time.sleep(backoff)
            backoff *= 2  # exponential back-off

    # If we reach here all retries failed
    raise RuntimeError(f"Failed to fetch ERP record {ident} after {max_attempts} attempts")

# ---------------------------------------------------------------------------
# NOTION HELPERS (copied from kareemdatabasetest.py)
# ---------------------------------------------------------------------------

def _fetch_all_children(block_id: str) -> List[dict]:
    """Return every child block, or [] if the API refuses (400/404/403)."""
    log.debug("Fetching children for block: %s", block_id[:8] + "...")
    children, cursor = [], None
    page_count = 0
    max_retries = 5
    
    while True:
        retry_count = 0
        while retry_count < max_retries:
            try:
                page_count += 1
                log.debug("Fetching page %d of children for block %s...", page_count, block_id[:8] + "...")
                resp = notion.blocks.children.list(
                    block_id, start_cursor=cursor, page_size=100
                )
                break  # Success, exit retry loop
                
            except RequestTimeoutError:
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 2 ** retry_count
                    log.warning("Request timeout when fetching children for block %s – retrying (%d/%d) in %ds", 
                              block_id[:8] + "...", retry_count, max_retries, wait_time)
                    time.sleep(wait_time)
                    continue
                else:
                    log.error("Repeated timeouts when fetching children for block %s – giving up", block_id[:8] + "...")
                    return children
                    
            except APIResponseError as e:
                status = getattr(e, "status", None)
                code = getattr(e, "code", None)
                
                # Handle rate limiting with exponential backoff
                if status == 429 or code == "rate_limited":
                    retry_count += 1
                    if retry_count < max_retries:
                        # Exponential backoff with jitter for rate limiting
                        base_wait = 2 ** retry_count
                        jitter = base_wait * 0.1 * (0.5 - time.time() % 1)  # Random jitter
                        wait_time = base_wait + jitter
                        log.warning("Rate limited when fetching children for block %s – retrying (%d/%d) in %.1fs", 
                                  block_id[:8] + "...", retry_count, max_retries, wait_time)
                        time.sleep(wait_time)
                        continue
                    else:
                        log.error("Rate limit exceeded for block %s after %d retries – giving up", 
                                block_id[:8] + "...", max_retries)
                        return children
                
                # Handle other errors (403, 404, etc.) - don't retry these
                else:
                    log.warning(
                        "Skipping inaccessible block's children: %s  (status %s, code %s)",
                        block_id,
                        status or "??",
                        code or "??",
                    )
                    return children
        else:
            # If we exhausted all retries without success
            log.error("Failed to fetch children for block %s after %d retries", block_id[:8] + "...", max_retries)
            return children

        children.extend(resp["results"])
        log.debug("Added %d children from page %d (total: %d)", len(resp["results"]), page_count, len(children))
        
        if resp.get("has_more"):
            cursor = resp["next_cursor"]
            log.debug("More children available, continuing with cursor: %s...", cursor[:8] if cursor else "None")
        else:
            break
    
    log.info("Retrieved %d total children for block %s", len(children), block_id[:8] + "...")
    return children

def _plain_text(block: dict) -> str:
    """Concatenate rich-text → plain string.
    
    This function handles all types of rich text objects:
    - text: regular text content
    - mention: page mentions, user mentions, etc. (extracts the display name)
    - equation: mathematical equations
    And any other rich text types by falling back to their plain_text field.
    """
    block_type = block.get("type", "")
    if block_type not in block:
        return ""
    
    rt = block[block_type].get("rich_text", [])
    text_parts = []
    
    for piece in rt:
        piece_type = piece.get("type", "text")
        
        if piece_type == "text":
            # Regular text content
            text_content = piece.get("text", {}).get("content", "")
            text_parts.append(text_content)
        elif piece_type == "mention":
            # Mentions (page links, user mentions, etc.) - use the display text
            mention_text = piece.get("plain_text", "")
            text_parts.append(mention_text)
        elif piece_type == "equation":
            # Mathematical equations - use the display text
            equation_text = piece.get("plain_text", "")
            text_parts.append(equation_text)
        else:
            # Fallback for any other rich text types - use plain_text field
            fallback_text = piece.get("plain_text", "")
            text_parts.append(fallback_text)
    
    return "".join(text_parts)

def _extract_block_metadata(block: dict) -> Dict[str, Any]:
    """Extract comprehensive metadata from a block."""
    metadata = {
        "id": block.get("id", ""),
        "type": block.get("type", ""),
        "created_time": block.get("created_time", ""),
        "last_edited_time": block.get("last_edited_time", ""),
        "has_children": block.get("has_children", False),
        "archived": block.get("archived", False),
    }
    
    if "parent" in block:
        metadata["parent_type"] = block["parent"].get("type", "")
        metadata["parent_id"] = block["parent"].get("page_id") or block["parent"].get("block_id", "")
    
    return metadata

def _extract_block_content(block: dict, notion_client=None) -> Dict[str, Any]:
    """Extract content based on block type."""
    block_id = block.get("id", "unknown")[:8] + "..."
    block_type = block.get("type", "")
    log.debug("Extracting content from %s block: %s", block_type, block_id)
    
    content = {"text": _plain_text(block)}
    
    if block_type not in block:
        log.warning("Block type '%s' not found in block data for %s", block_type, block_id)
        return content
    
    block_data = block[block_type]
    
    if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"]:
        content["rich_text"] = json.dumps(block_data.get("rich_text", []))
    elif block_type == "toggle":
        content["rich_text"] = json.dumps(block_data.get("rich_text", []))
    else:
        if "rich_text" in block_data:
            content["rich_text"] = json.dumps(block_data.get("rich_text", []))
    
    return content

class NotionDatabaseToCSV:
    def __init__(self, api_key: str):
        """Initialize the Notion client with API key"""
        global notion
        self.notion = Client(auth=api_key)
        notion = self.notion
    
    def extract_database_id_from_url(self, database_url: str) -> str:
        """Extract database ID from Notion database URL"""
        if "notion.so" in database_url:
            parts = database_url.split('/')
            for part in reversed(parts):
                if part and len(part) >= 32:
                    db_id = part.split('?')[0]
                    return db_id.replace('-', '')
        
        if len(database_url) == 32:
            return database_url
        
        raise ValueError("Invalid Notion database URL format")

    def extract_all_blocks_using_working_algorithm(self, page_id: str) -> List[Dict[str, Any]]:
        """Extract all blocks using the EXACT working algorithm from test_all_blocks_extractor"""
        all_blocks = []
        block_count = 0
        
        def dfs(bid: str, depth: int = 0) -> None:
            nonlocal block_count
            
            children = _fetch_all_children(bid)
            
            for i, child in enumerate(children):
                child_id = child.get("id", "unknown")[:8] + "..."
                child_type = child.get("type", "unknown")
                
                metadata = _extract_block_metadata(child)
                content = _extract_block_content(child, self.notion)
                
                block_record = {
                    **metadata,
                    **content,
                    "depth": depth,
                    "full_block_json": "" if not log.isEnabledFor(logging.DEBUG) else json.dumps(child, indent=2, ensure_ascii=False)
                }
                
                all_blocks.append(block_record)
                block_count += 1
                
                if child.get("has_children"):
                    dfs(child["id"], depth + 1)
        
        dfs(page_id)
        return all_blocks

    def _find_technical_ecp_block(self, start_block_id: str) -> Optional[dict]:
        """Depth-first search for the first block whose plain text starts with 'Technical ECP'."""

        def dfs(bid: str) -> Optional[dict]:
            children = _fetch_all_children(bid)
            for child in children:
                text = _plain_text(child).strip()
                if text.lower().startswith("technical ecp"):
                    return child
                if child.get("has_children"):
                    found = dfs(child["id"])
                    if found:
                        return found
            return None

        return dfs(start_block_id)

    def extract_technical_ecp_only(self, page_id: str) -> List[Dict[str, Any]]:
        """Locate the 'Technical ECP' block and return that block and all of its descendants."""

        target_block = self._find_technical_ecp_block(page_id)
        if not target_block:
            return []

        result: List[Dict[str, Any]] = []

        metadata = _extract_block_metadata(target_block)
        content = _extract_block_content(target_block, self.notion)
        result.append({
            **metadata,
            **content,
            "depth": 0,
            "full_block_json": "" if not log.isEnabledFor(logging.DEBUG) else json.dumps(target_block, indent=2, ensure_ascii=False)
        })

        descendant_blocks = self.extract_all_blocks_using_working_algorithm(target_block["id"])
        for blk in descendant_blocks:
            blk["depth"] += 1
            if not log.isEnabledFor(logging.DEBUG):
                blk["full_block_json"] = ""
        result.extend(descendant_blocks)

        return result

    def _clean_value_text(self, text: str) -> str:
        """Clean up introductory prefixes and common noise from extracted text."""
        if not text:
            return text
        
        # Remove common prefixes
        prefixes_to_remove = [
            "> Value Below 🔻",
            "Value Below 🔻", 
            "> Value Below",
            "Value Below",
            "🔻",
            "> 🔻"
        ]
        
        cleaned = text
        for prefix in prefixes_to_remove:
            if cleaned.strip().startswith(prefix):
                cleaned = cleaned.replace(prefix, "", 1).lstrip()
                break
        
        return cleaned.strip()

    def _process_page(self, page_index: int, total_pages: int, page: dict) -> Optional[Dict[str, Any]]:
        """Process a single Notion page and return structured data."""

        try:
            page_name = "Untitled"
            for prop_name, prop_data in page["properties"].items():
                if prop_data.get("type") == "title":
                    title_data = prop_data.get('title', [])
                    # Use the same rich text extraction logic as _plain_text function
                    text_parts = []
                    for text_obj in title_data:
                        if text_obj:
                            piece_type = text_obj.get("type", "text")
                            if piece_type == "text":
                                text_content = text_obj.get("text", {}).get("content", "")
                                text_parts.append(text_content)
                            elif piece_type == "mention":
                                mention_text = text_obj.get("plain_text", "")
                                text_parts.append(mention_text)
                            else:
                                # Fallback to plain_text for any other types
                                fallback_text = text_obj.get("plain_text", "")
                                text_parts.append(fallback_text)
                    
                    clean_value = "".join(text_parts).strip()
                    if clean_value:
                        page_name = clean_value
                        break

            filtered_blocks = self.extract_technical_ecp_only(page["id"])

            if not filtered_blocks:
                return None

            parameter_name = ""
            conditional_logic = []

            i = 0
            n_blocks = len(filtered_blocks)
            while i < n_blocks:
                blk = filtered_blocks[i]
                text = blk.get("text", "").strip()
                btype = blk.get("type")

                if btype == "toggle" and text.lower().startswith("technical ecp parameter name"):
                    parts = text.split(":", 1)
                    if len(parts) == 2:
                        parameter_name = parts[1].strip()

                elif btype == "toggle" and "condition" in text.lower():
                    condition_depth = blk.get("depth", 0)
                    condition_text = text.replace("[toggle]", "").strip()
                    if condition_text.lower().startswith("condition "):
                        condition_text = condition_text[10:].strip()
                    
                    # Use original fast algorithm with simple numbering
                    j = i + 1
                    values = []
                    current_number = 1  # Simple counter for numbered items
                    
                    while j < n_blocks and filtered_blocks[j]["depth"] > condition_depth:
                        inner_blk = filtered_blocks[j]
                        inner_text = inner_blk.get("text", "").strip()
                        inner_type = inner_blk.get("type", "")
                        
                        if inner_text:
                            if inner_type == "numbered_list_item":
                                # Add the number prefix for numbered list items
                                formatted_text = f"{current_number}. {inner_text}"
                                values.append(formatted_text)
                                current_number += 1
                            elif inner_type == "bulleted_list_item":
                                # Add bullet for bulleted list items
                                formatted_text = f"- {inner_text}"
                                values.append(formatted_text)
                            else:
                                # Regular text - just add as is
                                values.append(inner_text)
                        j += 1
                    
                    # Join with newlines for better structure and clean up
                    value_text = "\n".join(values)
                    value_text = self._clean_value_text(value_text)
                    
                    conditional_logic.append({
                        "condition": condition_text,
                        "value": value_text
                    })
                    i = j - 1
                
                i += 1

            identifier = f"{page_name.replace(' ', '_')}"

            return {
                "identifier": identifier,
                "parameter": parameter_name,
                "conditionalLogic": conditional_logic
            }

        except Exception as e:
            log.error("Error processing page %s: %s", page.get("id", "unknown"), e)
            return None

# ---------------------------------------------------------------------------
# Fetch ERP data
# ---------------------------------------------------------------------------

def gather_erp_data() -> List[Dict[str, Any]]:
    """Fetch all ERP GPTPromptParameter records concurrently using a thread pool.

    This significantly speeds up the slow sequential network calls by
    parallelising the `fetch_one` requests.  The number of worker threads is
    automatically chosen based on the number of IDs (capped at 32) to avoid
    overwhelming the ERP backend.
    """
    logging.info("Fetching ERP data for prompt '%s' (multithreaded)", PROMPT_NAME)

    # Early cancellation check before starting
    if cancel_event and cancel_event.is_set():
        logging.info("ERP data gathering cancelled before starting")
        return []

    ids = fetch_ids()
    if not ids:
        logging.warning("No ERP IDs found – returning empty list")
        return []

    # Thread-pool size heuristic: at most 32, but not more than len(ids)
    max_workers = min(5, len(ids))

    records: List[Dict[str, Any]] = []
    completed_count = 0
    
    progress_lock = threading.Lock()  # Thread-safe progress updates
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all fetch tasks first
        futures = {executor.submit(fetch_one, ident): ident for ident in ids}

        # As each future completes, convert and append
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(ids), desc="ERP records"):
            # Check cancellation more frequently
            if cancel_event and cancel_event.is_set():
                logging.info("ERP data gathering cancelled, shutting down thread pool...")
                # Cancel remaining futures
                for remaining_future in futures:
                    remaining_future.cancel()
                executor.shutdown(wait=False)
                break
                
            ident = futures[future]
            try:
                raw = future.result()
                converted = convert_record(raw)
                records.append(converted)
                
                # Thread-safe progress update
                with progress_lock:
                    completed_count += 1
                    # Update global progress if callback is available (70-80% range for ERP)
                    if progress_callback and len(ids) > 0:
                        progress_pct = 70 + int((completed_count / len(ids)) * 10)  # 70-80%
                        progress_callback("Fetching ERP data", progress_pct, f"Retrieved {completed_count}/{len(ids)} ERP records")
                    
            except Exception as e:
                logging.error("Failed to fetch ERP ID %s: %s", ident, e)
                # Still increment completed count for failed records to maintain progress accuracy
                with progress_lock:
                    completed_count += 1

    logging.info("Fetched %d ERP records", len(records))
    return records

# ---------------------------------------------------------------------------
# Fetch Notion data
# ---------------------------------------------------------------------------

def gather_notion_data() -> List[Dict[str, Any]]:
    logging.info("Fetching Notion data from database %s", DATABASE_URL)
    
    # Early cancellation check before starting
    if cancel_event and cancel_event.is_set():
        logging.info("Notion data gathering cancelled before starting")
        return []
        
    processor = NotionDatabaseToCSV(NOTION_TOKEN)

    database_id = processor.extract_database_id_from_url(DATABASE_URL)
    database_info = processor.notion.databases.retrieve(database_id)

    to_be_validated_prop = None
    technical_validated_prop = None
    
    for prop_name in database_info.get("properties", {}):
        prop_name_lower = prop_name.lower().strip()
        if prop_name_lower == "to be validated":
            to_be_validated_prop = prop_name
        elif prop_name_lower == "technical validated":
            technical_validated_prop = prop_name

    filter_payload: Dict[str, Any] | None = None
    if to_be_validated_prop and technical_validated_prop:
        # Build compound filter: "To be Validated" is true AND "Technical Validated" is false
        to_be_validated_info = database_info["properties"][to_be_validated_prop]
        technical_validated_info = database_info["properties"][technical_validated_prop]
        
        to_be_validated_type = to_be_validated_info.get("type")
        technical_validated_type = technical_validated_info.get("type")
        
        # Build first condition: "To be Validated" is true
        to_be_validated_filter = None
        if to_be_validated_type == "checkbox":
            to_be_validated_filter = {"property": to_be_validated_prop, "checkbox": {"equals": True}}
        elif to_be_validated_type in {"status", "select"}:
            truthy_labels = {"true", "yes", "validated", "done", "complete"}
            chosen = "True"
            for opt in to_be_validated_info.get(to_be_validated_type, {}).get("options", []):
                if opt.get("name", "").lower() in truthy_labels:
                    chosen = opt["name"]
                    break
            to_be_validated_filter = {"property": to_be_validated_prop, to_be_validated_type: {"equals": chosen}}
        
        # Build second condition: "Technical Validated" is false
        technical_validated_filter = None
        if technical_validated_type == "checkbox":
            technical_validated_filter = {"property": technical_validated_prop, "checkbox": {"equals": False}}
        elif technical_validated_type in {"status", "select"}:
            falsy_labels = {"false", "no", "not validated", "pending", "incomplete"}
            chosen = "False"
            for opt in technical_validated_info.get(technical_validated_type, {}).get("options", []):
                if opt.get("name", "").lower() in falsy_labels:
                    chosen = opt["name"]
                    break
            technical_validated_filter = {"property": technical_validated_prop, technical_validated_type: {"equals": chosen}}
        
        # Combine both conditions with AND logic
        if to_be_validated_filter and technical_validated_filter:
            filter_payload = {
                "and": [
                    to_be_validated_filter,
                    technical_validated_filter
                ]
            }
    elif to_be_validated_prop:
        # Fallback: if only "To be Validated" column exists, use original logic
        prop_info = database_info["properties"][to_be_validated_prop]
        prop_type = prop_info.get("type")
        if prop_type == "checkbox":
            filter_payload = {"property": to_be_validated_prop, "checkbox": {"equals": True}}
        elif prop_type in {"status", "select"}:
            truthy_labels = {"true", "yes", "validated", "done", "complete"}
            chosen = "True"
            for opt in prop_info.get(prop_type, {}).get("options", []):
                if opt.get("name", "").lower() in truthy_labels:
                    chosen = opt["name"]
                    break
            filter_payload = {"property": to_be_validated_prop, prop_type: {"equals": chosen}}

    pages: List[Dict[str, Any]] = []
    has_more = True
    cursor = None
    while has_more:
        # Check cancellation during page fetching
        if cancel_event and cancel_event.is_set():
            logging.info("Notion page fetching cancelled")
            return []
            
        query_kwargs: Dict[str, Any] = {"database_id": database_id, "page_size": 100}
        if filter_payload:
            query_kwargs["filter"] = filter_payload
        if cursor:
            query_kwargs["start_cursor"] = cursor
        resp = processor.notion.databases.query(**query_kwargs)
        pages.extend(resp["results"])
        has_more = resp.get("has_more", False)
        cursor = resp.get("next_cursor")

    # Process pages concurrently using thread pool
    records: List[Dict[str, Any]] = []
    max_workers = min(2, len(pages))  # Cap at 2 to be more respectful of Notion API rate limits
    completed_count = 0
    progress_lock = threading.Lock()  # Thread-safe progress updates
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all page processing tasks
        futures = {
            executor.submit(processor._process_page, idx + 1, len(pages), page): (idx, page)
            for idx, page in enumerate(pages)
        }
        
        # Collect results as they complete
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(pages), desc="Notion pages"):
            # Check cancellation more frequently
            if cancel_event and cancel_event.is_set():
                logging.info("Notion data processing cancelled, shutting down thread pool...")
                # Cancel remaining futures
                for remaining_future in futures:
                    remaining_future.cancel()
                executor.shutdown(wait=False)
                break
                
            idx, page = futures[future]
            try:
                obj = future.result()
                if obj:
                    records.append(obj)
                
                # Thread-safe progress update
                with progress_lock:
                    completed_count += 1
                    # Update global progress if callback is available (5-65% range for Notion, 60% of total)
                    if progress_callback and len(pages) > 0:
                        progress_pct = 5 + int((completed_count / len(pages)) * 60)  # 5-65%
                        progress_callback("Fetching Notion data", progress_pct, f"Processed {completed_count}/{len(pages)} Notion pages")
                    
                # Small delay to be respectful of API rate limits
                time.sleep(0.1)
                    
            except Exception as e:
                page_id = page.get("id", "unknown")
                logging.error("Failed to process Notion page %s (index %d): %s", page_id, idx, e)
                # Still increment completed count for failed pages to maintain progress accuracy
                with progress_lock:
                    completed_count += 1
    
    logging.info("Processed %d Notion pages, extracted %d records", len(pages), len(records))
    return records

# ---------------------------------------------------------------------------
# Google Sheets Helper
# ---------------------------------------------------------------------------

def split_large_text(text: str, max_chars: int = 45000) -> List[str]:
    """Split large text into chunks that fit within Google Sheets cell limits."""
    if len(text) <= max_chars:
        return [text]  # Return single item list if no splitting needed
    
    chunks = []
    remaining = text
    
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
            
        # Try to split at a reasonable boundary (like a comma or newline)
        split_point = max_chars
        
        # Look for good split points (in order of preference)
        for boundary in ['\n', ',', ' ', '"']:
            last_boundary = remaining.rfind(boundary, 0, max_chars)
            if last_boundary > max_chars * 0.8:  # Only use if it's not too early
                split_point = last_boundary + 1
                break
        
        chunk = remaining[:split_point]
        chunks.append(chunk)
        remaining = remaining[split_point:]
    
    return chunks

def create_shared_google_sheet(data_rows: List[List[str]], section_headers: List[int] = None) -> str:
    """Create a Google Sheet with comparison data and share it with anyone who has the link."""
    try:
        # Set up credentials and authorize
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        print("Setting up Google Sheets client...")
        creds = Credentials.from_service_account_info(GOOGLE_SHEETS_CREDENTIALS, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # Create new spreadsheet
        sheet_title = f"ERP-Notion Comparison {time.strftime('%Y-%m-%d %H:%M')}"
        print(f"Creating spreadsheet: {sheet_title}")
        
        try:
            spreadsheet = gc.create(sheet_title)
            print(f"✅ Spreadsheet created with ID: {spreadsheet.id}")
        except Exception as create_error:
            print(f"❌ Failed to create spreadsheet:")
            print(f"Create error: {type(create_error).__name__}: {str(create_error)}")
            raise create_error
            
        worksheet = spreadsheet.sheet1
        
        # Add header and data
        print("Adding header row...")
        worksheet.update('A1:D1', [['Parameter', 'Notion JSON', 'ERP JSON', 'Claude Comparison']])
        
        # Add data in batches (Google Sheets API has limits)
        print(f"Adding {len(data_rows)} data rows...")
        batch_size = 100
        for i in range(0, len(data_rows), batch_size):
            batch = data_rows[i:i + batch_size]
            start_row = i + 2  # +2 because we start after header row
            end_row = start_row + len(batch) - 1
            range_name = f'A{start_row}:D{end_row}'  # Updated to D for 4 columns
            worksheet.update(range_name, batch)
            logging.info(f"Updated rows {start_row}-{end_row}")
        
        # Format the sheet
        print("Formatting header...")
        worksheet.format('A1:D1', {  # Updated to D1 for 4 columns
            'backgroundColor': {'red': 0.94, 'green': 0.94, 'blue': 0.94},  # #f0f0f0
            'textFormat': {'bold': True}
        })
        
        # ────────────────────────────────────────────────────────────────
        # COLUMN WIDTH / ALIGNMENT / WRAP  (match historical Code.gs style)
        # ────────────────────────────────────────────────────────────────
        print("Applying custom column widths & wrapping…")

        # Column pixel sizes – A:200px, B-D:400px (0-based indices)
        sheet_id = worksheet._properties["sheetId"]
        width_requests = []
        for idx, px in enumerate([200, 400, 400, 500]):  # Updated for 4 columns
            width_requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
        })
        
        # Apply batch update for column widths
        if width_requests:
            worksheet.spreadsheet.batch_update({"requests": width_requests})

        # Vertical align top & wrap text for entire data range
        end_row = len(data_rows) + 1  # +1 for header
        data_range = f"A1:D{end_row}"  # Updated to D for 4 columns
        worksheet.format(data_range, {
            "verticalAlignment": "TOP",
            "wrapStrategy": "WRAP"
        })
        
        # Format section headers (red highlighting, merge cells, no wrap)
        if section_headers:
            print("Formatting section headers...")
            for header_row_index in section_headers:
                actual_row = header_row_index + 2  # +2 because we start after main header row
                
                # Merge cells across the row for the header
                try:
                    worksheet.merge_cells(f'A{actual_row}:D{actual_row}')  # Updated to D for 4 columns
                    print(f"✅ Merged cells for section header at row {actual_row}")
                except Exception as merge_error:
                    print(f"⚠️ Could not merge cells for row {actual_row}: {merge_error}")
                
                # Apply red background and bold formatting
                header_range = f'A{actual_row}:D{actual_row}'  # Updated to D for 4 columns
                worksheet.format(header_range, {
                    'backgroundColor': {'red': 0.9, 'green': 0.2, 'blue': 0.2},  # Red background
                    'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},  # White text
                    'horizontalAlignment': 'CENTER',
                    'wrapStrategy': 'CLIP'  # No wrapping for headers
                })
                print(f"✅ Applied red formatting to section header at row {actual_row}")
        
        # Share with anyone who has the link (instead of domain restriction)
        print("Attempting to share with anyone who has the link...")
        try:
            # Share with anyone who has the link (no domain restriction)
            spreadsheet.share('', perm_type='anyone', role='reader', with_link=True)
            print("✅ Successfully shared with anyone who has the link")
        except Exception as share_error:
            print(f"⚠️  Public sharing failed: {type(share_error).__name__}: {str(share_error)}")
            print("Sheet created but not publicly shared")
        
        sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        print(f"✅ Google Sheet created: {sheet_url}")
        print(f"📋 Share this URL with your organization: {sheet_url}")
        return sheet_url
        
    except Exception as e:
        logging.error(f"Failed to create Google Sheet: {e}")
        # Fallback to local Excel file
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Comparison"
        ws.append(["Parameter", "Notion JSON", "ERP JSON", "Claude Comparison"])
        for row in data_rows:
            ws.append(row)
        wb.save(XLSX_OUT)
        logging.info(f"Fallback: Local Excel file saved to {XLSX_OUT}")
        return str(XLSX_OUT.resolve())

# ---------------------------------------------------------------------------
# JSON Processing Helper
# ---------------------------------------------------------------------------

def replace_logical_operators(obj):
    """Recursively replace || with OR and && with AND in JSON data."""
    if isinstance(obj, dict):
        return {key: replace_logical_operators(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [replace_logical_operators(item) for item in obj]
    elif isinstance(obj, str):
        # Replace logical operators in string values
        return obj.replace('||', ' OR ').replace('&&', ' AND ')
    else:
        return obj

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    # Early cancellation check
    if cancel_event and cancel_event.is_set():
        logging.info("Main function cancelled before starting")
        return

    # Fetch from both sources
    notion_records = gather_notion_data()
    
    # Check cancellation after Notion data
    if cancel_event and cancel_event.is_set():
        logging.info("Process cancelled after Notion data fetch")
        return
        
    erp_records = gather_erp_data()
    
    # Check cancellation after ERP data
    if cancel_event and cancel_event.is_set():
        logging.info("Process cancelled after ERP data fetch")
        return

    # Build lookup by parameter name (case-insensitive)
    notion_lookup = {rec["parameter"].lower(): rec for rec in notion_records if rec.get("parameter")}
    erp_lookup = {rec["parameter"].lower(): rec for rec in erp_records if rec.get("parameter")}

    all_params = sorted(set(notion_lookup) | set(erp_lookup))
    logging.info("Total parameters: Notion=%d, ERP=%d, Combined=%d", len(notion_lookup), len(erp_lookup), len(all_params))

    # Separate parameters by type
    both_params = []  # Parameters in both Notion and ERP
    notion_only_params = []  # Parameters only in Notion
    erp_only_params = []  # Parameters only in ERP
    
    for param in all_params:
        if param in notion_lookup and param in erp_lookup:
            both_params.append(param)
        elif param in notion_lookup:
            notion_only_params.append(param)
        elif param in erp_lookup:
            erp_only_params.append(param)
    
    both_params.sort()
    notion_only_params.sort()
    erp_only_params.sort()
    
    logging.info("Parameter distribution - Both: %d, Notion-only: %d, ERP-only: %d", 
                len(both_params), len(notion_only_params), len(erp_only_params))

    # Prepare data rows for Google Sheets
    data_rows = []
    section_headers = []  # Track section header row indices
    
    def add_parameter_rows(param: str, notion_json: dict, erp_json: dict, comparison_text: str):
        """Helper function to add parameter rows with proper formatting"""
        # Apply logical operator replacements to Notion JSON before pretty-printing
        processed_notion_json = replace_logical_operators(notion_json) if notion_json else notion_json

        # Convert to pretty-printed JSON strings for Google Sheets (readable format)
        notion_json_str = json.dumps(processed_notion_json, ensure_ascii=False, indent=2)
        erp_json_str = json.dumps(erp_json, ensure_ascii=False, indent=2)
        
        # Split large JSON strings to avoid 50k character limit
        notion_chunks = split_large_text(notion_json_str)
        erp_chunks = split_large_text(erp_json_str)
        
        # Determine how many rows we need (max of notion and erp chunks)
        max_chunks = max(len(notion_chunks), len(erp_chunks))
        
        # Create rows - first row has parameter name and comparison, subsequent rows are continuations
        for j in range(max_chunks):
            if j == 0:
                # First row: include parameter name and comparison
                row = [
                    param,
                    notion_chunks[j] if j < len(notion_chunks) else "",
                    erp_chunks[j] if j < len(erp_chunks) else "",
                    comparison_text
                ]
            else:
                # Continuation rows: empty parameter name and comparison
                row = [
                    f"  └─ {param} (cont.)",  # Indented continuation indicator
                    notion_chunks[j] if j < len(notion_chunks) else "",
                    erp_chunks[j] if j < len(erp_chunks) else "",
                    ""  # Empty comparison for continuation rows
                ]
            
            data_rows.append(row)
    
    # 1. First: Parameters that exist in both sources (comparison)
    for i, param in enumerate(tqdm(both_params, desc="Comparing matched parameters")):
        # Check cancellation during comparison loop
        if cancel_event and cancel_event.is_set():
            logging.info("Process cancelled during comparison at %d/%d", i, len(both_params))
            return
            
        notion_json = notion_lookup[param]
        erp_json = erp_lookup[param]
        comparison_text = compare_with_claude(notion_json, erp_json)
        
        add_parameter_rows(param, notion_json, erp_json, comparison_text)

    # 2. Second: Add section header for Notion-only parameters
    if notion_only_params:
        section_headers.append(len(data_rows))  # Record the row index for formatting
        data_rows.append(["=== NOTION-ONLY PARAMETERS ===", "", "", ""])
        
        for i, param in enumerate(tqdm(notion_only_params, desc="Processing Notion-only parameters")):
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                logging.info("Process cancelled during Notion-only processing at %d/%d", i, len(notion_only_params))
                return
                
            notion_json = notion_lookup[param]
            add_parameter_rows(param, notion_json, {}, "Parameter missing in ERP")

    # 3. Third: Add section header for ERP-only parameters
    if erp_only_params:
        section_headers.append(len(data_rows))  # Record the row index for formatting
        data_rows.append(["=== ERP-ONLY PARAMETERS ===", "", "", ""])
        
        for i, param in enumerate(tqdm(erp_only_params, desc="Processing ERP-only parameters")):
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                logging.info("Process cancelled during ERP-only processing at %d/%d", i, len(erp_only_params))
                return
                
            erp_json = erp_lookup[param]
            add_parameter_rows(param, {}, erp_json, "Parameter missing in Notion")

    # Final cancellation check before creating sheet
    if cancel_event and cancel_event.is_set():
        logging.info("Process cancelled before creating Google Sheet")
        return

    # Create shared Google Sheet
    sheet_url = create_shared_google_sheet(data_rows, section_headers)
    logging.info("🎉 Comparison complete! Sheet URL: %s", sheet_url)


if __name__ == "__main__":
    main() 