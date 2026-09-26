"""Resume pending jobs; stale processing jobs require stopped web workers."""

import argparse

from backend.services.knowledge_service import recover_pending_documents

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-stale-processing", action="store_true",
                        help="Only after stopping all web workers")
    args = parser.parse_args()
    print(f"processed {recover_pending_documents(args.include_stale_processing)} pending documents")
