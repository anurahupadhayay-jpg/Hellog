"""
Monetization & Timer System Module.
Manages user time balances, recharges, and cost calculations.
1 Rupee = 1 hour of usage time.
"""

import logging
from typing import Dict, Any, Optional

from bot.database import db
from bot.config import COST_PER_HOUR, WARNING_THRESHOLD_MINUTES, DEFAULT_FREE_MINUTES

logger = logging.getLogger(__name__)


class Monetization:
    """Handle monetization: time balances, recharges, and cost tracking."""

    def __init__(self):
        self.cost_per_hour = COST_PER_HOUR
        self.warning_threshold = WARNING_THRESHOLD_MINUTES
        self.free_minutes = DEFAULT_FREE_MINUTES

    def calculate_cost(self, minutes: float) -> float:
        """
        Calculate cost in Rupees for given minutes.
        1 Rupee = 1 hour (60 minutes)
        """
        hours = minutes / 60
        return round(hours * self.cost_per_hour, 2)

    def calculate_minutes(self, rupees: float) -> float:
        """
        Calculate minutes for given Rupees.
        1 Rupee = 60 minutes
        """
        return round((rupees / self.cost_per_hour) * 60, 2)

    def get_remaining_minutes(self, user_id: int) -> float:
        """Get user's remaining time in minutes."""
        return db.get_remaining_minutes(user_id)

    def has_sufficient_time(self, user_id: int, required_minutes: float = 5) -> bool:
        """Check if user has sufficient time for an operation."""
        remaining = self.get_remaining_minutes(user_id)
        return remaining >= required_minutes

    def should_warn(self, user_id: int) -> bool:
        """Check if user should be warned about low balance."""
        remaining = self.get_remaining_minutes(user_id)
        return 0 < remaining < self.warning_threshold

    def get_warning_message(self, user_id: int) -> str:
        """Generate warning message for low balance."""
        remaining = self.get_remaining_minutes(user_id)
        if remaining <= 0:
            return (
                f"⚠️ **Time Balance Depleted!**\n\n"
                f"Your account has no remaining time.\n"
                f"Please recharge to continue using the bot.\n\n"
                f"💰 **Rate:** ₹{self.cost_per_hour} per hour\n"
                f"Use /recharge to add time."
            )
        elif remaining < self.warning_threshold:
            return (
                f"⚠️ **Low Time Balance Warning**\n\n"
                f"You have only **{remaining:.1f} minutes** remaining.\n"
                f"Please consider recharging soon to avoid interruptions.\n\n"
                f"💰 **Rate:** ₹{self.cost_per_hour} per hour\n"
                f"Use /recharge to add more time."
            )
        return ""

    def get_balance_message(self, user_id: int) -> str:
        """Generate formatted balance status message."""
        balance = db.get_time_balance(user_id)
        remaining = balance["remaining_minutes"]
        total = balance["total_minutes"]
        used = balance["used_minutes"]

        # Calculate percentage
        percentage = (remaining / total * 100) if total > 0 else 0

        # Status emoji
        if remaining <= 0:
            status = "🔴"
        elif remaining < self.warning_threshold:
            status = "🟡"
        else:
            status = "🟢"

        # Progress bar
        filled = int(percentage / 5)
        bar = "█" * filled + "░" * (20 - filled)

        hours = int(remaining // 60)
        minutes = int(remaining % 60)

        return (
            f"{status} **Your Time Balance**\n\n"
            f"[{bar}] {percentage:.1f}%\n\n"
            f"⏱ **Remaining:** {hours}h {minutes}m ({remaining:.1f} minutes)\n"
            f"📊 **Total Purchased:** {total:.1f} minutes\n"
            f"📈 **Used:** {used:.1f} minutes\n\n"
            f"💰 **Rate:** ₹{self.cost_per_hour} per hour\n"
            f"Use /recharge to add more time."
        )

    def deduct_time(self, user_id: int, minutes: float) -> bool:
        """
        Deduct time from user's balance.
        Returns True if successful, False if insufficient balance.
        """
        return db.deduct_time(user_id, minutes)

    def add_time(self, user_id: int, minutes: float, amount_rupees: float = 0,
                 payment_method: str = None, transaction_id: str = None) -> bool:
        """Add time to user's balance (admin or payment gateway)."""
        return db.add_time(user_id, minutes, amount_rupees, payment_method, transaction_id)

    def recharge(self, user_id: int, rupees: float,
                 payment_method: str = "manual",
                 transaction_id: str = None) -> Dict[str, Any]:
        """
        Process a recharge for a user.

        Args:
            user_id: Telegram user ID
            rupees: Amount in Rupees
            payment_method: Payment method used
            transaction_id: Transaction reference

        Returns:
            Dict with recharge result
        """
        minutes = self.calculate_minutes(rupees)

        success = db.add_time(
            user_id=user_id,
            minutes=minutes,
            amount_rupees=rupees,
            payment_method=payment_method,
            transaction_id=transaction_id
        )

        if success:
            new_balance = self.get_remaining_minutes(user_id)
            return {
                "success": True,
                "rupees": rupees,
                "minutes_added": minutes,
                "new_balance": new_balance,
                "message": (
                    f"✅ **Recharge Successful!**\n\n"
                    f"💰 **Amount:** ₹{rupees}\n"
                    f"⏱ **Time Added:** {minutes:.1f} minutes\n"
                    f"💳 **Method:** {payment_method}\n"
                    f"📊 **New Balance:** {new_balance:.1f} minutes"
                )
            }
        else:
            return {
                "success": False,
                "message": "❌ Recharge failed. Please try again or contact support."
            }

    def get_recharge_options(self) -> str:
        """Get formatted recharge options message."""
        options = [
            (10, self.calculate_minutes(10)),
            (50, self.calculate_minutes(50)),
            (100, self.calculate_minutes(100)),
            (500, self.calculate_minutes(500)),
        ]

        lines = [
            "💰 **Recharge Options**\n",
            f"**Rate:** ₹{self.cost_per_hour} = 60 minutes\n",
            "Available plans:"
        ]

        for rupees, minutes in options:
            hours = int(minutes // 60)
            mins = int(minutes % 60)
            lines.append(f"  • ₹{rupees} → {hours}h {mins}m")

        lines.extend([
            "\n📌 **How to recharge:**",
            "Contact admin with /support command",
            "or use /recharge [amount] for manual entry."
        ])

        return "\n".join(lines)

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive user statistics."""
        balance = db.get_time_balance(user_id)
        history = db.get_upload_history(user_id, limit=5)

        total_uploads = len(history)
        successful = sum(1 for h in history if h["status"] == "completed")
        failed = sum(1 for h in history if h["status"] == "failed")

        return {
            "balance": balance,
            "total_uploads": total_uploads,
            "successful_uploads": successful,
            "failed_uploads": failed,
            "recent_history": history
        }


# Global monetization instance
monetization = Monetization()
