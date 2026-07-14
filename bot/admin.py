"""
Admin Module.
Handles admin authentication, secret login, broadcast messaging,
and administrative commands for bot management.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from bot.config import MAIN_ADMIN_ID, ADMIN_USERNAME, ADMIN_PASSWORD
from bot.database import db

logger = logging.getLogger(__name__)


class AdminManager:
    """Handle admin operations and authentication."""

    def __init__(self):
        self.main_admin_id = MAIN_ADMIN_ID
        self.admin_username = ADMIN_USERNAME
        self.admin_password = ADMIN_PASSWORD

    # ── Admin Authentication ──────────────────────────────────────────────────

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is an admin (main or temporary)."""
        if user_id == self.main_admin_id:
            return True
        return db.is_user_admin(user_id)

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate admin credentials."""
        return username == self.admin_username and password == self.admin_password

    def create_temporary_session(self, admin_user_id: int,
                                  granted_by_user_id: int = None,
                                  duration_hours: int = 24) -> bool:
        """Create a temporary admin session for emergency access."""
        try:
            success = db.create_admin_session(
                admin_user_id=admin_user_id,
                granted_by_user_id=granted_by_user_id,
                duration_hours=duration_hours
            )
            if success:
                logger.warning(
                    f"Temporary admin session created for {admin_user_id} "
                    f"by {granted_by_user_id}, expires in {duration_hours}h"
                )
            return success
        except Exception as e:
            logger.error(f"Error creating admin session: {e}")
            return False

    def revoke_session(self, admin_user_id: int) -> bool:
        """Revoke a temporary admin session."""
        return db.revoke_admin_session(admin_user_id)

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast_message(self, bot, message_text: str,
                                 from_user_id: int,
                                 reply_markup=None) -> Dict[str, int]:
        """
        Broadcast a message to all users in the database.

        Returns:
            Dict with "sent" and "failed" counts
        """
        if not self.is_admin(from_user_id):
            return {"sent": 0, "failed": 0, "error": "Unauthorized"}

        users = db.get_all_users()
        sent = 0
        failed = 0

        # Send in batches to avoid rate limiting
        batch_size = 25
        delay_between_batches = 2  # seconds

        for i in range(0, len(users), batch_size):
            batch = users[i:i + batch_size]

            for user in batch:
                user_id = user["user_id"]
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        reply_markup=reply_markup,
                        disable_web_page_preview=True
                    )
                    sent += 1
                    await asyncio.sleep(0.1)  # Small delay between messages
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to send broadcast to {user_id}: {e}")

            # Delay between batches
            if i + batch_size < len(users):
                await asyncio.sleep(delay_between_batches)

        logger.info(f"Broadcast complete: {sent} sent, {failed} failed")
        return {"sent": sent, "failed": failed}

    async def broadcast_to_user(self, bot, user_id: int, message_text: str,
                                 reply_markup=None) -> bool:
        """Send a message to a specific user (admin only)."""
        try:
            await bot.send_message(
                chat_id=user_id,
                text=message_text,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            return False

    # ── User Management ───────────────────────────────────────────────────────

    def get_user_info(self, target_user_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a user."""
        user = db.get_user(target_user_id)
        if not user:
            return None

        balance = db.get_time_balance(target_user_id)
        history = db.get_upload_history(target_user_id, limit=10)

        return {
            "user": user,
            "balance": balance,
            "recent_uploads": history
        }

    def ban_user(self, target_user_id: int) -> bool:
        """Ban a user from using the bot."""
        return db.ban_user(target_user_id, banned=True)

    def unban_user(self, target_user_id: int) -> bool:
        """Unban a user."""
        return db.ban_user(target_user_id, banned=False)

    def add_time_to_user(self, target_user_id: int, minutes: float,
                         amount_rupees: float = 0) -> bool:
        """Add time to a user's balance (admin operation)."""
        return db.add_time(target_user_id, minutes, amount_rupees)

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_bot_stats(self) -> Dict[str, Any]:
        """Get comprehensive bot statistics."""
        return db.get_stats()

    def get_stats_message(self) -> str:
        """Get formatted statistics message for admin."""
        stats = self.get_bot_stats()

        return (
            f"📊 **Bot Statistics**\n\n"
            f"👥 **Total Users:** {stats.get('total_users', 0)}\n"
            f"📹 **Total Uploads:** {stats.get('total_uploads', 0)}\n"
            f"✅ **Completed:** {stats.get('completed_uploads', 0)}\n"
            f"❌ **Failed:** {stats.get('failed_uploads', 0)}\n"
            f"📋 **Queue Length:** {stats.get('queue_length', 0)}\n"
            f"💰 **Total Revenue:** ₹{stats.get('total_revenue', 0):.2f}"
        )

    def get_admin_help(self) -> str:
        """Get admin command help message."""
        return (
            "🔐 **Admin Commands**\n\n"
            "**Authentication:**\n"
            "  `/admin_login [username] [password]` - Emergency admin login\n"
            "  `/admin_logout` - Revoke admin access\n\n"
            "**Broadcast:**\n"
            "  Reply to any message with `/broadcast` to send to all users\n"
            "  `/broadcast_text [message]` - Send text to all users\n\n"
            "**User Management:**\n"
            "  `/user_info [user_id]` - Get user details\n"
            "  `/ban [user_id]` - Ban a user\n"
            "  `/unban [user_id]` - Unban a user\n"
            "  `/add_time [user_id] [minutes]` - Add time to user\n\n"
            "**Statistics:**\n"
            "  `/stats` - Bot statistics\n"
            "  `/queue_status` - Check upload queue"
        )


# Global admin manager instance
admin_manager = AdminManager()
