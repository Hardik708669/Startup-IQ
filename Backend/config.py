"""Shared environment-based configuration."""

import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "Startup IQ"
APP_TAGLINE = "An analytical indicator, not a magic prediction."
AI_API_KEY = os.getenv("AI_API_KEY")
