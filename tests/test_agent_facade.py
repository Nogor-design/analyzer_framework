from ta_foundation.agent.facade import AnalysisFacade
import os

def test_facade():
    facade = AnalysisFacade()
    print("Testing Ingest...")
    # Using a known data folder if it exists, otherwise just checking if it fails gracefully
    try:
        # Check for a data folder in the project
        data_path = "discovery" # Just a placeholder
        res = facade.ingest(data_path)
        print(f"Ingest Result: {res}")
    except Exception as e:
        print(f"Ingest failed as expected (or actual error): {e}")

if __name__ == "__main__":
    test_facade()
