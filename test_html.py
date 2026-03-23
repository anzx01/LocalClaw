#!/usr/bin/env python3
"""Test script to generate web UI HTML."""

from localclaw.channels.web import get_web_ui_html

if __name__ == "__main__":
    try:
        html = get_web_ui_html()
        print("HTML generated successfully!")
        print(f"HTML length: {len(html)} characters")
        print("\nFirst 500 characters:")
        print(html[:500])
        print("\nLast 500 characters:")
        print(html[-500:])
    except Exception as e:
        print(f"Error generating HTML: {e}")
        import traceback
        traceback.print_exc()