"""Compatibility shim — historical entry point for the Streamlit app.

The real implementation lives in the `menu_ocr/` package. This file exists so
`streamlit run menu_compare_claude.py` (the legacy command, plus any bookmarks /
scripts that point at it) keeps working.

For new setups prefer:  streamlit run menu_ocr/app.py
"""
from menu_ocr.app import main

main()
