"""
YouTube OAuth 2.0 Handler Module.
Manages Google authentication flow for each user to upload to their own YouTube channel.
"""

import logging
import asyncio
import json
import hashlib
from typing import Optional, Dict, Any
from urllib.parse import quote

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError

from bot.config import (
    CLIENT_SECRETS_PATH,
    OAUTH_REDIRECT_URI,
    YOUTUBE_SCOPES,
    CREDENTIALS_DIR
)
from bot.database import db

logger = logging.getLogger(__name__)

# Store active flows (in production, use Redis or database)
_active_flows: Dict[int, Flow] = {}


class YouTubeOAuthHandler:
    """Handle OAuth 2.0 authentication for YouTube API."""

    def __init__(self):
        self.client_secrets_path = CLIENT_SECRETS_PATH
        self.redirect_uri = OAUTH_REDIRECT_URI
        self.scopes = YOUTUBE_SCOPES

    def _create_flow(self, user_id: int) -> Flow:
        """Create a new OAuth flow for a user."""
        try:
            flow = Flow.from_client_secrets_file(
                self.client_secrets_path,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri,
                state=self._generate_state(user_id)
            )
            return flow
        except Exception as e:
            logger.error(f"Error creating OAuth flow: {e}")
            raise

    def _generate_state(self, user_id: int) -> str:
        """Generate a state parameter to prevent CSRF attacks."""
        data = f"{user_id}:{asyncio.get_event_loop().time()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def get_authorization_url(self, user_id: int) -> str:
        """
        Generate the Google OAuth authorization URL for a user.
        Returns the URL the user must visit to authorize the bot.
        """
        try:
            flow = self._create_flow(user_id)
            _active_flows[user_id] = flow

            auth_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent"  # Force consent to get refresh token
            )

            logger.info(f"Generated auth URL for user {user_id}")
            return auth_url

        except Exception as e:
            logger.error(f"Error generating auth URL for user {user_id}: {e}")
            raise

    async def exchange_code(self, user_id: int, authorization_code: str) -> bool:
        """
        Exchange authorization code for access/refresh tokens.
        Called when user sends back the code from the auth URL.
        """
        try:
            flow = _active_flows.get(user_id)
            if not flow:
                logger.error(f"No active flow found for user {user_id}")
                return False

            # Run the token exchange in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: flow.fetch_token(code=authorization_code)
            )

            credentials = flow.credentials

            # Build token data dictionary
            token_data = {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": list(credentials.scopes) if credentials.scopes else self.scopes,
                "expiry": credentials.expiry.isoformat() if credentials.expiry else None
            }

            # Get user's YouTube channel info
            channel_info = await self._get_channel_info(credentials)
            email = channel_info.get("email", "")
            channel_id = channel_info.get("channel_id", "")

            # Save to database
            success = db.save_oauth_token(user_id, token_data, email, channel_id)

            # Clean up the flow
            _active_flows.pop(user_id, None)

            if success:
                logger.info(f"OAuth token saved for user {user_id}, channel: {channel_id}")
            return success

        except Exception as e:
            logger.error(f"Error exchanging code for user {user_id}: {e}")
            # Clean up on failure
            _active_flows.pop(user_id, None)
            return False

    async def _get_channel_info(self, credentials: Credentials) -> Dict[str, str]:
        """Get YouTube channel information using credentials."""
        try:
            loop = asyncio.get_event_loop()
            youtube = await loop.run_in_executor(
                None,
                lambda: build("youtube", "v3", credentials=credentials, cache_discovery=False)
            )

            # Get channel info
            response = await loop.run_in_executor(
                None,
                lambda: youtube.channels().list(part="snippet,contentDetails", mine=True).execute()
            )

            channels = response.get("items", [])
            if channels:
                channel = channels[0]
                return {
                    "channel_id": channel.get("id", ""),
                    "title": channel.get("snippet", {}).get("title", ""),
                    "email": channel.get("snippet", {}).get("customUrl", "")
                }
            return {}

        except Exception as e:
            logger.error(f"Error getting channel info: {e}")
            return {}

    async def get_credentials(self, user_id: int) -> Optional[Credentials]:
        """
        Get valid credentials for a user.
        Automatically refreshes token if expired.
        """
        try:
            token_info = db.get_oauth_token(user_id)
            if not token_info:
                logger.warning(f"No token found for user {user_id}")
                return None

            token_data = token_info["token_data"]

            # Create credentials object
            credentials = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_data.get("scopes", self.scopes)
            )

            # Check if token needs refresh
            if credentials.expired and credentials.refresh_token:
                logger.info(f"Refreshing token for user {user_id}")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, credentials.refresh, Request())

                # Update stored token
                new_token_data = {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token,
                    "token_uri": credentials.token_uri,
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "scopes": list(credentials.scopes) if credentials.scopes else self.scopes,
                    "expiry": credentials.expiry.isoformat() if credentials.expiry else None
                }
                db.save_oauth_token(user_id, new_token_data,
                                   token_info.get("email"), token_info.get("channel_id"))

            return credentials

        except RefreshError as e:
            logger.error(f"Token refresh failed for user {user_id}: {e}")
            # Token is revoked, delete it
            db.delete_oauth_token(user_id)
            return None
        except Exception as e:
            logger.error(f"Error getting credentials for {user_id}: {e}")
            return None

    async def get_youtube_service(self, user_id: int):
        """Get authenticated YouTube API service for a user."""
        credentials = await self.get_credentials(user_id)
        if not credentials:
            return None

        try:
            loop = asyncio.get_event_loop()
            youtube = await loop.run_in_executor(
                None,
                lambda: build("youtube", "v3", credentials=credentials, cache_discovery=False)
            )
            return youtube
        except Exception as e:
            logger.error(f"Error building YouTube service for {user_id}: {e}")
            return None

    async def revoke_credentials(self, user_id: int) -> bool:
        """Revoke and delete credentials for a user."""
        try:
            credentials = await self.get_credentials(user_id)
            if credentials:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, credentials.revoke, Request())

            db.delete_oauth_token(user_id)
            logger.info(f"Credentials revoked for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error revoking credentials for {user_id}: {e}")
            # Still delete from database even if revoke fails
            db.delete_oauth_token(user_id)
            return True


# Global OAuth handler instance
oauth_handler = YouTubeOAuthHandler()
