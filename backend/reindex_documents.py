"""Compatibility entry point; implementation is in backend.cli.reindex_documents."""

from backend.cli.reindex_documents import main as main, reindex_all as reindex_all

if __name__ == "__main__":
    main()
