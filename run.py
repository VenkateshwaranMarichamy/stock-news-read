#!/usr/bin/env python3
"""
Run the MoneyControl scraper directly.

Usage (from the project folder with venv active):
    python run.py
    python run.py "https://www.moneycontrol.com/news/..."
    python run.py --file urls.txt
    python run.py --output my_results.json
"""
import sys
import os

# Add the project root to sys.path so imports work without pip install
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from moneycontrol_scraper.cli import main

main()
