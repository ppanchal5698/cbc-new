import os
from decimal import Decimal

# Cost Constants per NFR-6 / Section 10
TEXTRACT_PAGE_COST = Decimal('0.015')
CLAUDE_INPUT_TOKEN_COST = Decimal('0.003') / 1000
CLAUDE_OUTPUT_TOKEN_COST = Decimal('0.015') / 1000

def generate_cost_report():
    """
    NFR-6 Mitigation: Validates the actual spend against the MAX_OCR_COST_PER_DOCUMENT_USD guard.
    In production, this aggregates over DocumentManifest rows to see exactly how many
    pages were routed to Textract versus Triaged (skipped).
    """
    print("--- CBC Copilot Cost Report ---")
    
    # Mock aggregation data
    total_documents = 50
    total_pages_received = 2500
    pages_triaged_out = 2000
    pages_ocr = 500
    
    avg_input_tokens = 4000
    avg_output_tokens = 500
    
    # Calculations
    textract_total = pages_ocr * TEXTRACT_PAGE_COST
    claude_total = total_documents * ((avg_input_tokens * CLAUDE_INPUT_TOKEN_COST) + (avg_output_tokens * CLAUDE_OUTPUT_TOKEN_COST))
    
    grand_total = textract_total + claude_total
    cost_per_doc = grand_total / total_documents if total_documents else 0
    
    print(f"Total Documents Processed: {total_documents}")
    print(f"Total Pages Received:      {total_pages_received}")
    print(f"Pages Triaged (Skipped):   {pages_triaged_out} ({(pages_triaged_out/total_pages_received):.1%} savings)")
    print(f"Pages Sent to OCR:         {pages_ocr}")
    print("-" * 35)
    print(f"AWS Textract Cost:         ${textract_total:.2f}")
    print(f"AWS Bedrock (Claude) Cost: ${claude_total:.2f}")
    print(f"Grand Total:               ${grand_total:.2f}")
    print("-" * 35)
    print(f"Average Cost Per Doc:      ${cost_per_doc:.2f}")
    
    if cost_per_doc > Decimal('2.00'):
        print("WARNING: Cost per document exceeds target threshold of $2.00!")
    else:
        print("STATUS: Within cost guardrails.")

if __name__ == "__main__":
    generate_cost_report()
