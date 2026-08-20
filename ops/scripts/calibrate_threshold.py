import numpy as np

def calibrate_confidence_thresholds():
    """
    R7 Mitigation: Calculates the optimal confidence threshold for human review.
    Instead of hardcoding 0.80, this script will evaluate historical extraction
    confidences against human corrections to plot a Flag-Rate vs Error-Escape-Rate curve.
    
    CBC Management then chooses an operating point (e.g. "Flag 20% of fields to catch 99% of errors").
    """
    print("--- Confidence Calibration Tool ---")
    
    # Mock data representing (confidence_score, was_error) pairs
    # In production, this data comes from the CorrectionEvent table.
    mock_events = [
        (0.99, False), (0.95, False), (0.85, False), (0.82, True), 
        (0.79, True),  (0.75, True),  (0.60, True),  (0.91, False)
    ]
    
    thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    
    print(f"{'Threshold':<10} | {'Flagged Rate':<15} | {'Escape Rate (Missed Errors)':<25}")
    print("-" * 55)
    
    total_events = len(mock_events)
    total_errors = sum(1 for _, is_error in mock_events if is_error)
    
    for t in thresholds:
        # A field is flagged for review if confidence < t
        flagged_count = sum(1 for conf, _ in mock_events if conf < t)
        flag_rate = flagged_count / total_events if total_events else 0
        
        # An error escapes if it was an error BUT confidence >= t (so it wasn't flagged)
        escaped_errors = sum(1 for conf, is_error in mock_events if is_error and conf >= t)
        escape_rate = escaped_errors / total_errors if total_errors else 0
        
        print(f"{t:<10.2f} | {flag_rate:<15.1%} | {escape_rate:<25.1%}")

    print("\nNext Step: Review this table with CBC Management to select the production threshold.")

if __name__ == "__main__":
    calibrate_confidence_thresholds()
