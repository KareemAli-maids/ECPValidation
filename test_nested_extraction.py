#!/usr/bin/env python3
"""
Test script to verify nested content extraction is working properly
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to the path so we can import merge_compare
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_nested_extraction():
    """Test the nested content extraction functionality"""
    
    # Check if we have the required environment variables
    required_vars = ["NOTION_TOKEN", "DATABASE_URL"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set up your .env file with NOTION_TOKEN and DATABASE_URL")
        return False
    
    try:
        from merge_compare import NotionDatabaseToCSV
        
        print("🔍 Testing nested content extraction...")
        
        # Initialize the processor
        processor = NotionDatabaseToCSV(os.getenv("NOTION_TOKEN"))
        
        # Extract database ID from URL
        database_url = os.getenv("DATABASE_URL")
        database_id = processor.extract_database_id_from_url(database_url)
        
        print(f"📊 Database ID: {database_id}")
        
        # Get the first few pages to test
        resp = processor.notion.databases.query(database_id=database_id, page_size=3)
        pages = resp["results"]
        
        print(f"📄 Found {len(pages)} pages to test")
        
        # Test processing each page
        for i, page in enumerate(pages):
            print(f"\n🔄 Testing page {i+1}...")
            
            try:
                result = processor._process_page(i+1, len(pages), page)
                
                if result:
                    print(f"✅ Parameter: {result.get('parameter', 'N/A')}")
                    print(f"   Conditions: {len(result.get('conditionalLogic', []))}")
                    
                    # Show the first condition's value to check nested content
                    conditions = result.get('conditionalLogic', [])
                    if conditions:
                        first_condition = conditions[0]
                        condition_text = first_condition.get('condition', 'N/A')
                        value_text = first_condition.get('value', 'N/A')
                        
                        print(f"   First condition: {condition_text}")
                        print(f"   Value preview: {value_text[:200]}...")
                        
                        # Check if we have nested content (multiple lines)
                        if '\n' in value_text:
                            lines = value_text.split('\n')
                            print(f"   ✅ Nested content detected: {len(lines)} lines")
                            for j, line in enumerate(lines[:3]):  # Show first 3 lines
                                print(f"      Line {j+1}: {line}")
                            if len(lines) > 3:
                                print(f"      ... and {len(lines) - 3} more lines")
                        else:
                            print(f"   ℹ️  Single line content")
                else:
                    print(f"   ❌ No result (may not have Technical ECP content)")
                    
            except Exception as e:
                print(f"   ❌ Error processing page: {e}")
        
        print(f"\n✅ Test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_nested_extraction()
    sys.exit(0 if success else 1) 