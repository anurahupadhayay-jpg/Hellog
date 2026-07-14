"""
Database module using SQLite.
Handles all CRUD operations for users, OAuth tokens, time balances, and upload history.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager

from bot.config import DATABASE_PATH, DEFAULT_FREE_MINUTES

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager for the bot."""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_database()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_database(self):
        """Initialize database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Users table - basic user info
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_banned INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0
                )
            """)

            # OAuth tokens table - stores encrypted credentials per user
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    user_id INTEGER PRIMARY KEY,
                    token_json TEXT NOT NULL,  -- Encrypted OAuth2 credentials as JSON
                    email TEXT,
                    channel_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Time balance table - tracks user's remaining paid time
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS time_balances (
                    user_id INTEGER PRIMARY KEY,
                    total_minutes REAL DEFAULT 0,
                    used_minutes REAL DEFAULT 0,
                    remaining_minutes REAL DEFAULT 0,
                    last_recharge_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Upload history table - tracks all upload jobs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upload_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    drive_link TEXT NOT NULL,
                    youtube_video_id TEXT,
                    status TEXT DEFAULT 'pending',  -- pending, downloading, uploading, completed, failed, cancelled
                    title TEXT,
                    description TEXT,
                    tags TEXT,
                    privacy_status TEXT DEFAULT 'private',
                    category_id TEXT,
                    thumbnail_path TEXT,
                    file_size_bytes INTEGER DEFAULT 0,
                    error_message TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Recharges table - payment history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recharges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_rupees REAL NOT NULL,
                    minutes_added REAL NOT NULL,
                    payment_method TEXT,
                    transaction_id TEXT,
                    status TEXT DEFAULT 'completed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Admin sessions table - for emergency admin login
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_user_id INTEGER NOT NULL,
                    granted_by_user_id INTEGER,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_active INTEGER DEFAULT 1
                )
            """)

            # Queue table - upload job queue
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upload_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    drive_link TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    tags TEXT,
                    privacy_status TEXT DEFAULT 'private',
                    category_id TEXT,
                    thumbnail_path TEXT,
                    status TEXT DEFAULT 'queued',  -- queued, processing, completed, failed
                    priority INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            logger.info("Database initialized successfully")

    # ── User Management ─────────────────────────────────────────────────────────

    def add_user(self, user_id: int, username: str = None, first_name: str = None,
                 last_name: str = None) -> bool:
        """Add a new user or update existing user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        first_name = excluded.first_name,
                        last_name = excluded.last_name,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, username, first_name, last_name))

                # Initialize time balance for new users with free trial
                cursor.execute("""
                    INSERT INTO time_balances (user_id, total_minutes, remaining_minutes)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO NOTHING
                """, (user_id, DEFAULT_FREE_MINUTES, DEFAULT_FREE_MINUTES))

                logger.info(f"User {user_id} added/updated successfully")
                return True
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")
            return False

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user details by ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all registered users."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE is_banned = 0")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    def is_user_admin(self, user_id: int) -> bool:
        """Check if user is main admin or has active admin session."""
        from bot.config import MAIN_ADMIN_ID
        if user_id == MAIN_ADMIN_ID:
            return True
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Check if user has active admin session
                cursor.execute("""
                    SELECT 1 FROM admin_sessions
                    WHERE admin_user_id = ? AND is_active = 1 AND expires_at > datetime('now')
                """, (user_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Error checking admin status for {user_id}: {e}")
            return False

    def ban_user(self, user_id: int, banned: bool = True) -> bool:
        """Ban or unban a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET is_banned = ? WHERE user_id = ?",
                    (1 if banned else 0, user_id)
                )
                return True
        except Exception as e:
            logger.error(f"Error banning user {user_id}: {e}")
            return False

    # ── OAuth Token Management ─────────────────────────────────────────────────

    def save_oauth_token(self, user_id: int, token_data: Dict[str, Any],
                         email: str = None, channel_id: str = None) -> bool:
        """Save OAuth credentials for a user."""
        try:
            token_json = json.dumps(token_data)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO oauth_tokens (user_id, token_json, email, channel_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        token_json = excluded.token_json,
                        email = excluded.email,
                        channel_id = excluded.channel_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, token_json, email, channel_id))
                logger.info(f"OAuth token saved for user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving OAuth token for {user_id}: {e}")
            return False

    def get_oauth_token(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve OAuth credentials for a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM oauth_tokens WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                if row:
                    return {
                        "user_id": row["user_id"],
                        "token_data": json.loads(row["token_json"]),
                        "email": row["email"],
                        "channel_id": row["channel_id"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"]
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting OAuth token for {user_id}: {e}")
            return None

    def has_oauth_token(self, user_id: int) -> bool:
        """Check if user has saved OAuth credentials."""
        return self.get_oauth_token(user_id) is not None

    def delete_oauth_token(self, user_id: int) -> bool:
        """Delete OAuth credentials for a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
                return True
        except Exception as e:
            logger.error(f"Error deleting OAuth token for {user_id}: {e}")
            return False

    # ── Time Balance Management ────────────────────────────────────────────────

    def get_time_balance(self, user_id: int) -> Dict[str, float]:
        """Get user's time balance details."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM time_balances WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                if row:
                    return {
                        "total_minutes": row["total_minutes"],
                        "used_minutes": row["used_minutes"],
                        "remaining_minutes": row["remaining_minutes"],
                        "last_recharge_at": row["last_recharge_at"]
                    }
                # Return zero balance if not found
                return {"total_minutes": 0, "used_minutes": 0, "remaining_minutes": 0, "last_recharge_at": None}
        except Exception as e:
            logger.error(f"Error getting time balance for {user_id}: {e}")
            return {"total_minutes": 0, "used_minutes": 0, "remaining_minutes": 0, "last_recharge_at": None}

    def add_time(self, user_id: int, minutes: float, amount_rupees: float = 0,
                 payment_method: str = None, transaction_id: str = None) -> bool:
        """Add time to user's balance (recharge)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Update or insert time balance
                cursor.execute("""
                    INSERT INTO time_balances (user_id, total_minutes, remaining_minutes, last_recharge_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        total_minutes = total_minutes + ?,
                        remaining_minutes = remaining_minutes + ?,
                        last_recharge_at = datetime('now'),
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, minutes, minutes, minutes, minutes))

                # Record recharge
                if amount_rupees > 0:
                    cursor.execute("""
                        INSERT INTO recharges (user_id, amount_rupees, minutes_added, payment_method, transaction_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (user_id, amount_rupees, minutes, payment_method, transaction_id))

                logger.info(f"Added {minutes} minutes to user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Error adding time for {user_id}: {e}")
            return False

    def deduct_time(self, user_id: int, minutes: float) -> bool:
        """Deduct time from user's balance."""
        try:
            balance = self.get_time_balance(user_id)
            if balance["remaining_minutes"] < minutes:
                logger.warning(f"Insufficient balance for user {user_id}")
                return False

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE time_balances
                    SET used_minutes = used_minutes + ?,
                        remaining_minutes = remaining_minutes - ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (minutes, minutes, user_id))
                logger.info(f"Deducted {minutes} minutes from user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Error deducting time for {user_id}: {e}")
            return False

    def get_remaining_minutes(self, user_id: int) -> float:
        """Get remaining minutes for a user."""
        balance = self.get_time_balance(user_id)
        return balance["remaining_minutes"]

    def has_sufficient_time(self, user_id: int, required_minutes: float = 0) -> bool:
        """Check if user has sufficient time balance."""
        return self.get_remaining_minutes(user_id) > required_minutes

    # ── Upload History ─────────────────────────────────────────────────────────

    def add_upload_job(self, user_id: int, drive_link: str, title: str = None,
                       description: str = None, tags: str = None,
                       privacy_status: str = "private", category_id: str = None,
                       thumbnail_path: str = None) -> int:
        """Add a new upload job and return the job ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO upload_history
                    (user_id, drive_link, title, description, tags, privacy_status,
                     category_id, thumbnail_path, status, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
                """, (user_id, drive_link, title, description, tags,
                      privacy_status, category_id, thumbnail_path))
                job_id = cursor.lastrowid
                logger.info(f"Upload job {job_id} added for user {user_id}")
                return job_id
        except Exception as e:
            logger.error(f"Error adding upload job for {user_id}: {e}")
            return -1

    def update_upload_status(self, job_id: int, status: str,
                             youtube_video_id: str = None,
                             file_size: int = None,
                             error_message: str = None) -> bool:
        """Update upload job status."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                completed_at = None
                if status in ("completed", "failed", "cancelled"):
                    completed_at = datetime.now().isoformat()

                cursor.execute("""
                    UPDATE upload_history
                    SET status = ?,
                        youtube_video_id = COALESCE(?, youtube_video_id),
                        file_size_bytes = COALESCE(?, file_size_bytes),
                        error_message = COALESCE(?, error_message),
                        completed_at = COALESCE(?, completed_at)
                    WHERE id = ?
                """, (status, youtube_video_id, file_size, error_message,
                      completed_at, job_id))
                return True
        except Exception as e:
            logger.error(f"Error updating upload job {job_id}: {e}")
            return False

    def get_upload_history(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get upload history for a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM upload_history
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, limit))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting upload history for {user_id}: {e}")
            return []

    # ── Queue Management ───────────────────────────────────────────────────────

    def add_to_queue(self, user_id: int, drive_link: str, title: str = None,
                     description: str = None, tags: str = None,
                     privacy_status: str = "private", category_id: str = None,
                     thumbnail_path: str = None, priority: int = 0) -> int:
        """Add an upload job to the queue."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO upload_queue
                    (user_id, drive_link, title, description, tags,
                     privacy_status, category_id, thumbnail_path, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, drive_link, title, description, tags,
                      privacy_status, category_id, thumbnail_path, priority))
                queue_id = cursor.lastrowid
                logger.info(f"Job {queue_id} added to queue for user {user_id}")
                return queue_id
        except Exception as e:
            logger.error(f"Error adding to queue for {user_id}: {e}")
            return -1

    def get_next_queue_item(self) -> Optional[Dict[str, Any]]:
        """Get the next pending item from the queue (highest priority first)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM upload_queue
                    WHERE status = 'queued' AND retry_count < 3
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"Error getting next queue item: {e}")
            return None

    def update_queue_status(self, queue_id: int, status: str,
                            error_message: str = None) -> bool:
        """Update queue item status."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                started_at = None
                if status == "processing":
                    started_at = datetime.now().isoformat()

                cursor.execute("""
                    UPDATE upload_queue
                    SET status = ?,
                        error_message = COALESCE(?, error_message),
                        started_at = COALESCE(?, started_at),
                        completed_at = CASE WHEN ? IN ('completed', 'failed') THEN datetime('now') ELSE completed_at END,
                        retry_count = CASE WHEN ? = 'failed' THEN retry_count + 1 ELSE retry_count END
                    WHERE id = ?
                """, (status, error_message, started_at, status, status, queue_id))
                return True
        except Exception as e:
            logger.error(f"Error updating queue item {queue_id}: {e}")
            return False

    def get_user_queue_position(self, user_id: int) -> int:
        """Get user's position in the queue."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) as position FROM upload_queue
                    WHERE status = 'queued'
                    AND created_at <= (SELECT created_at FROM upload_queue WHERE user_id = ? AND status = 'queued' ORDER BY created_at LIMIT 1)
                """, (user_id,))
                row = cursor.fetchone()
                return row["position"] if row else 0
        except Exception as e:
            logger.error(f"Error getting queue position for {user_id}: {e}")
            return 0

    def get_queue_length(self) -> int:
        """Get total number of queued items."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM upload_queue WHERE status = 'queued'")
                row = cursor.fetchone()
                return row["count"] if row else 0
        except Exception as e:
            logger.error(f"Error getting queue length: {e}")
            return 0

    # ── Admin Sessions ─────────────────────────────────────────────────────────

    def create_admin_session(self, admin_user_id: int,
                             granted_by_user_id: int = None,
                             duration_hours: int = 24) -> bool:
        """Create temporary admin session."""
        try:
            expires_at = datetime.now() + timedelta(hours=duration_hours)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Deactivate existing sessions for this user
                cursor.execute("""
                    UPDATE admin_sessions SET is_active = 0 WHERE admin_user_id = ?
                """, (admin_user_id,))
                # Create new session
                cursor.execute("""
                    INSERT INTO admin_sessions (admin_user_id, granted_by_user_id, expires_at)
                    VALUES (?, ?, ?)
                """, (admin_user_id, granted_by_user_id, expires_at.isoformat()))
                logger.info(f"Admin session created for {admin_user_id}, expires at {expires_at}")
                return True
        except Exception as e:
            logger.error(f"Error creating admin session for {admin_user_id}: {e}")
            return False

    def revoke_admin_session(self, admin_user_id: int) -> bool:
        """Revoke admin session."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE admin_sessions SET is_active = 0 WHERE admin_user_id = ?
                """, (admin_user_id,))
                return True
        except Exception as e:
            logger.error(f"Error revoking admin session for {admin_user_id}: {e}")
            return False

    # ── Statistics ─────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get bot statistics for admin dashboard."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                stats = {}

                # Total users
                cursor.execute("SELECT COUNT(*) as count FROM users")
                stats["total_users"] = cursor.fetchone()["count"]

                # Total uploads
                cursor.execute("SELECT COUNT(*) as count FROM upload_history")
                stats["total_uploads"] = cursor.fetchone()["count"]

                # Completed uploads
                cursor.execute("SELECT COUNT(*) as count FROM upload_history WHERE status = 'completed'")
                stats["completed_uploads"] = cursor.fetchone()["count"]

                # Failed uploads
                cursor.execute("SELECT COUNT(*) as count FROM upload_history WHERE status = 'failed'")
                stats["failed_uploads"] = cursor.fetchone()["count"]

                # Total revenue
                cursor.execute("SELECT COALESCE(SUM(amount_rupees), 0) as total FROM recharges WHERE status = 'completed'")
                stats["total_revenue"] = cursor.fetchone()["total"]

                # Queue length
                stats["queue_length"] = self.get_queue_length()

                return stats
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}


# Global database instance
db = Database()
