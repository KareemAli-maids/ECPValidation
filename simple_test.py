#!/usr/bin/env python3
"""
Simple test to verify nested content extraction logic
"""

def test_nested_content_recursively(blocks, start_index, target_depth):
    """Test version of the recursive function"""
    content_parts = []
    i = start_index
    numbered_counters = {}  # Track numbering per depth level
    
    while i < len(blocks) and blocks[i]["depth"] > target_depth:
        block = blocks[i]
        block_type = block.get("type", "")
        block_depth = block.get("depth", 0)
        block_text = block.get("text", "").strip()
        
        # Skip empty blocks
        if not block_text:
            i += 1
            continue
        
        # Calculate indentation based on depth relative to target
        indent_level = block_depth - target_depth - 1
        indent = "    " * indent_level  # 4 spaces per level
        
        # Handle different block types with appropriate formatting
        if block_type == "bulleted_list_item":
            content_parts.append(f"{indent}- {block_text}")
        elif block_type == "numbered_list_item":
            # Track numbering per depth level for proper sequential numbering
            if block_depth not in numbered_counters:
                numbered_counters[block_depth] = 1
            else:
                numbered_counters[block_depth] += 1
            
            number = numbered_counters[block_depth]
            content_parts.append(f"{indent}{number}. {block_text}")
        elif block_type == "paragraph":
            content_parts.append(f"{indent}{block_text}")
        else:
            # For any other block type, just add the text if it exists
            content_parts.append(f"{indent}{block_text}")
        
        i += 1
    
    return "\n".join(content_parts)

def test_nested_structure():
    """Test the nested structure handling"""
    
    # Simulate the block structure from your example
    test_blocks = [
        {
            "type": "toggle",
            "text": "Technical ECP Parameter Name: testParam",
            "depth": 0
        },
        {
            "type": "toggle", 
            "text": "Condition: status == 'active'",
            "depth": 1
        },
        {
            "type": "bulleted_list_item",
            "text": "Explain to the customer that the customer's payment breakdown is as follows:",
            "depth": 2
        },
        {
            "type": "numbered_list_item",
            "text": "A payment of AED SDR1.Amount for the first installment of the 2-year visa and AED WPSProcessingAmount for Maid and Client Support, due on SDR1.DueDate",
            "depth": 3
        },
        {
            "type": "numbered_list_item", 
            "text": "A payment of AED for the second installment of the 2-year visa and AED WPSProcessingAmount for Maid and Client Support, due on SDR2.DueDate",
            "depth": 3
        },
        {
            "type": "numbered_list_item",
            "text": "A payment of AED SDR3.Amount for the third installment of the 2-year visa, due on SDR3.DueDate",
            "depth": 3
        }
    ]
    
    print("🔍 Testing nested content extraction...")
    print("\nInput block structure:")
    for i, block in enumerate(test_blocks):
        indent = "  " * block["depth"]
        print(f"{i}: {indent}{block['type']}: {block['text'][:50]}...")
    
    # Test extracting content under the condition (depth 1)
    condition_depth = 1
    start_index = 2  # Start after the condition toggle
    
    result = test_nested_content_recursively(test_blocks, start_index, condition_depth)
    
    print(f"\n✅ Extracted content under condition (depth {condition_depth}):")
    print("=" * 60)
    print(result)
    print("=" * 60)
    
    # Verify the structure
    lines = result.split('\n')
    print(f"\n📊 Analysis:")
    print(f"   Total lines: {len(lines)}")
    
    for i, line in enumerate(lines):
        if line.strip():
            leading_spaces = len(line) - len(line.lstrip())
            print(f"   Line {i+1}: {leading_spaces} spaces -> '{line.strip()[:40]}...'")
    
    # Check if numbered lists are properly nested
    has_bullet = any(line.strip().startswith('-') for line in lines)
    has_numbered = any(line.strip().startswith(('1.', '2.', '3.')) for line in lines)
    has_indented_numbered = any(line.startswith('    ') and line.strip().startswith(('1.', '2.', '3.')) for line in lines)
    
    print(f"\n🎯 Structure validation:")
    print(f"   ✅ Has bulleted list: {has_bullet}")
    print(f"   ✅ Has numbered lists: {has_numbered}")
    print(f"   ✅ Has indented numbered lists: {has_indented_numbered}")
    
    if has_bullet and has_numbered and has_indented_numbered:
        print(f"\n🎉 SUCCESS: Nested structure preserved correctly!")
        return True
    else:
        print(f"\n❌ FAILED: Nested structure not preserved correctly")
        return False

if __name__ == "__main__":
    success = test_nested_structure()
    exit(0 if success else 1) 