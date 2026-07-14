#!/usr/bin/env python3
"""
Launcher script for the SaaS YouTube Uploader Bot.
This script loads environment variables and starts the bot.
"""

import os
import sys
import logging
from pathlib import Path

# Add the project directory to the path
project_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(project_dir))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_file = project_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"✅ Loaded environment from {env_file}")
    else:
        print(f"⚠️  No .env file found at {env_file}")
        print("   Copy .env.example to .env and fill in your credentials.")
except ImportError:
    print("⚠️  python-dotenv not installed. Using system environment variables.")

# Import and run the main bot
from bot.main import main

if __name__ == "__main__":
    main()
