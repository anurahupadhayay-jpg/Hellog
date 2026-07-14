"""
Monetization & Timer System Module.
Manages user time balances, tier-based recharges, and UPIGateway integration.
Includes returning user discount logic.
"""

import logging
from typing import Dict, Any, Optional

from bot.database import db
from bot.config import WARNING_THRESHOLD_MINUTES, DEFAULT_FREE_MINUTES

logger = logging.getLogger(__name__)

# UPIGateway API Configuration
UPIGATEWAY_API_KEY = "B7d72c62-39a9-4036-93ec-2c74e73b8e2c"

# --- SUBSCRIPTION PLANS ---
PLANS = {
    "plan_1": {"name": "1 Hour", "first_price": 1, "renew_price": 1, "first_hours": 1, "renew_hours": 2},
    "plan_2": {"name": "2 Days", "first_price": 10, "renew_price": 5, "first_hours": 48, "renew_hours": 48},
    "plan_3": {"name": "5 Days", "first_price": 15, "renew_price": 7, "first_hours": 120, "renew_hours": 120},
    "plan_4": {"name": "1 Month", "first_price": 50, "renew_price": 25, "first_hours": 720, "renew_hours": 720},
    "plan_5": {"name": "1 Year", "first_price": 499, "renew_price": 499, "first_hours": 8760, "renew_hours": 8760}
}

class Monetization:
    """Handle monetization: time balances, recharges, and cost tracking."""

    def __init__(self):
        self.warning_threshold = WARNING_THRESHOLD_MINUTES
        self.free_minutes = DEFAULT_FREE_MINUTES

    def is_returning_user(self, user_id: int) -> bool:
        """Check if user has recharged before (Returning Customer)."""
        balance = db.get_time_balance(user_id)
        # If total_minutes used/acquired is more than default free minutes, they are an old user
        return balance["total_minutes"] > self.free_minutes

    def calculate_minutes(self, rupees: float, user_id: int = None) -> float:
        """
        Calculate minutes for given Rupees based on the user's plan tier.
        """
        if user_id:
            is_old = self.is_returning_user(user_id)
            for key, plan in PLANS.items():
                price = plan["renew_price"] if is_old else plan["first_price"]
                if price == rupees:
                    hours = plan["renew_hours"] if is_old else plan["first_hours"]
                    return float(hours * 60)
        
        # Fallback ratio if custom amount is sent outside the plans
        return float(rupees * 60)

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
                f"Use /plans to view our premium options."
            )
        elif remaining < self.warning_threshold:
            return (
                f"⚠️ **Low Time Balance Warning**\n\n"
                f"You have only **{remaining:.1f} minutes** remaining.\n"
                f"Please consider recharging soon to avoid interruptions.\n\n"
                f"Use /plans to view discount offers."
            )
        return ""

    def get_balance_message(self, user_id: int) -> str:
        """Generate formatted balance status message."""
        balance = db.get_time_balance(user_id)
        remaining = balance["remaining_minutes"]
        total = balance["total_minutes"]
        used = balance["used_minutes"]

        percentage = min((remaining / total * 100), 100) if total > 0 else 0

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
        time_display = f"{hours}h {minutes}m ({remaining:.1f} minutes)"

        return (
            f"{status} **Your Time Balance**\n\n"
            f"[{bar}] {percentage:.1f}%\n\n"
            f"⏱ **Remaining:** {time_display}\n"
            f"📊 **Total Purchased:** {total:.1f} minutes\n"
            f"📈 **Used:** {used:.1f} minutes\n\n"
            f"Use /plans to add more time or upgrade."
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
                 payment_method: str = "UPIGateway",
                 transaction_id: str = None) -> Dict[str, Any]:
        """
        Process a recharge for a user with smart plan detection.
        """
        minutes = self.calculate_minutes(rupees, user_id)

        success = db.add_time(
            user_id=user_id,
            minutes=minutes,
            amount_rupees=rupees,
            payment_method=payment_method,
            transaction_id=transaction_id
        )

        if success:
            new_balance = self.get_remaining_minutes(user_id)
            time_added_str = f"{minutes:.1f} minutes"
            
            return {
                "success": True,
                "rupees": rupees,
                "minutes_added": minutes,
                "new_balance": new_balance,
                "message": (
                    f"✅ **Recharge Successful!**\n\n"
                    f"💰 **Amount:** ₹{rupees}\n"
                    f"⏱ **Time Added:** {time_added_str}\n"
                    f"💳 **Method:** {payment_method}\n"
                    f"🎉 **Thank you for choosing our Premium Service!**"
                )
            }
        else:
            return {
                "success": False,
                "message": "❌ Recharge failed. Please try again or contact support."
            }

    def get_recharge_options(self, user_id: int = None) -> str:
        """Get formatted recharge options message with smart pricing."""
        is_old = self.is_returning_user(user_id) if user_id else False
        
        if is_old:
            lines = ["🎉 **Special 50% Discount For You! (Returning User)**\n"]
        else:
            lines = ["💰 **Premium Plans (Upgrade Now)**\n"]

        for plan_id, plan in PLANS.items():
            price = plan["renew_price"] if is_old else plan["first_price"]
            hours = plan["renew_hours"] if is_old else plan["first_hours"]
                
            lines.append(f"🔹 **{plan['name']}**")
            lines.append(f"   💸 Price: ₹{price}")
            
            if hours >= 24:
                lines.append(f"   ⏱ Time: {int(hours/24)} Days\n")
            else:
                lines.append(f"   ⏱ Time: {hours} Hours\n")

        lines.extend([
            "📌 **How to recharge:**",
            "Send the command `/buy [Amount]`",
            "Example for Monthly Plan: `/buy 50` (or 25 if discounted)"
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
