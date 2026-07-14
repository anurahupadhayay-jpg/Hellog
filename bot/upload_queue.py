"""
Upload Queue System Module.
Manages a FIFO queue for processing uploads one by one,
preventing server overload when multiple users send videos simultaneously.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable
from datetime import datetime

from bot.database import db
from bot.drive_downloader import downloader, DriveDownloadProgress
from bot.youtube_uploader import uploader, UploadProgress
from bot.monetization import monetization

logger = logging.getLogger(__name__)


class UploadQueue:
    """
    FIFO queue system for processing video uploads sequentially.
    Ensures server resources aren't overwhelmed by concurrent uploads.
    """

    def __init__(self):
        self.is_processing = False
        self.current_job: Optional[Dict[str, Any]] = None
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._progress_callbacks: Dict[int, Callable] = {}  # user_id -> callback

    async def start(self):
        """Start the queue processor in the background."""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._process_queue())
            logger.info("Upload queue processor started")

    async def stop(self):
        """Stop the queue processor gracefully."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Upload queue processor stopped")

    async def add_job(self, user_id: int, drive_link: str, title: str,
                      description: str = None, tags: str = None,
                      privacy_status: str = "private", category_id: str = "22",
                      thumbnail_path: str = None,
                      progress_callback: Callable = None) -> int:
        """
        Add a new upload job to the queue.

        Returns:
            queue_id: ID of the queued job
        """
        # Check if user has sufficient time before adding to queue
        remaining = monetization.get_remaining_minutes(user_id)
        if remaining <= 0:
            return -1  # Insufficient balance

        queue_id = db.add_to_queue(
            user_id=user_id,
            drive_link=drive_link,
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy_status,
            category_id=category_id,
            thumbnail_path=thumbnail_path
        )

        if queue_id > 0 and progress_callback:
            self._progress_callbacks[user_id] = progress_callback

        # Ensure processor is running
        await self.start()

        return queue_id

    async def _process_queue(self):
        """Main queue processing loop."""
        while not self._stop_event.is_set():
            try:
                if not self.is_processing:
                    next_job = db.get_next_queue_item()
                    if next_job:
                        await self._process_job(next_job)
                    else:
                        # No jobs in queue, wait before checking again
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=5.0
                        )
                else:
                    # Currently processing, wait a bit
                    await asyncio.sleep(1)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                await asyncio.sleep(5)

    async def _process_job(self, job: Dict[str, Any]):
        """Process a single upload job from the queue."""
        queue_id = job["id"]
        user_id = job["user_id"]
        drive_link = job["drive_link"]

        self.is_processing = True
        self.current_job = job

        # Mark as processing
        db.update_queue_status(queue_id, "processing")

        # Add upload history record
        history_id = db.add_upload_job(
            user_id=user_id,
            drive_link=drive_link,
            title=job["title"],
            description=job["description"],
            tags=job["tags"],
            privacy_status=job["privacy_status"],
            category_id=job["category_id"],
            thumbnail_path=job["thumbnail_path"]
        )

        progress_callback = self._progress_callbacks.get(user_id)

        try:
            # ── STEP 1: Download from Google Drive ──────────────────────
            logger.info(f"[Queue {queue_id}] Starting download for user {user_id}")

            download_progress_data = {"last_update": 0, "message": None}

            async def on_download_progress(progress: DriveDownloadProgress):
                """Handle download progress updates."""
                if progress_callback:
                    try:
                        message = progress.get_status_message()
                        # Only update if message changed significantly
                        current_time = asyncio.get_event_loop().time()
                        if current_time - download_progress_data["last_update"] >= 3:
                            download_progress_data["last_update"] = current_time
                            await progress_callback({
                                "stage": "downloading",
                                "message": message,
                                "progress": progress.percentage,
                                "drive_progress": progress
                            })
                    except Exception as e:
                        logger.error(f"Download progress callback error: {e}")

            download_result = await downloader.download(
                drive_url=drive_link,
                user_id=user_id,
                progress_callback=on_download_progress
            )

            if not download_result["success"]:
                error_msg = download_result.get("error", "Download failed")
                logger.error(f"[Queue {queue_id}] Download failed: {error_msg}")
                db.update_queue_status(queue_id, "failed", error_msg)
                db.update_upload_status(history_id, "failed", error_message=error_msg)

                if progress_callback:
                    await progress_callback({
                        "stage": "failed",
                        "message": f"❌ **Download Failed**\n\n{error_msg}",
                        "error": error_msg
                    })
                return

            file_path = download_result["file_path"]
            file_size = download_result["size"]

            # Update file size in history
            db.update_upload_status(history_id, "downloading", file_size=file_size)

            # ── STEP 2: Deduct time for the upload ──────────────────────
            # Calculate time cost (minimum 1 minute per upload)
            time_cost = max(1, file_size / (100 * 1024 * 1024))  # ~100MB per minute base rate
            deducted = monetization.deduct_time(user_id, time_cost)
            if not deducted:
                # Should have been checked earlier, but double-check
                logger.warning(f"[Queue {queue_id}] Insufficient time for user {user_id}")
                await downloader.cleanup(file_path)
                db.update_queue_status(queue_id, "failed", "Insufficient time balance")
                db.update_upload_status(history_id, "failed", error_message="Insufficient time balance")
                return

            # ── STEP 3: Upload to YouTube ──────────────────────────────
            logger.info(f"[Queue {queue_id}] Starting YouTube upload for user {user_id}")

            upload_progress_data = {"last_update": 0, "combined_message": None}

            async def on_upload_progress(progress: UploadProgress):
                """Handle upload progress updates."""
                if progress_callback:
                    try:
                        message = progress.get_status_message()
                        current_time = asyncio.get_event_loop().time()
                        if current_time - upload_progress_data["last_update"] >= 3:
                            upload_progress_data["last_update"] = current_time
                            await progress_callback({
                                "stage": "uploading",
                                "message": message,
                                "progress": progress.percentage,
                                "upload_progress": progress
                            })
                    except Exception as e:
                        logger.error(f"Upload progress callback error: {e}")

            upload_result = await uploader.upload_video(
                user_id=user_id,
                file_path=file_path,
                title=job["title"] or download_result.get("filename", "Untitled Video"),
                description=job["description"],
                tags=job["tags"],
                category_id=job["category_id"],
                privacy_status=job["privacy_status"],
                thumbnail_path=job["thumbnail_path"],
                progress_callback=on_upload_progress
            )

            # ── STEP 4: Cleanup ─────────────────────────────────────────
            # ALWAYS cleanup the downloaded file
            await downloader.cleanup(file_path)

            # Cleanup thumbnail if exists
            if job["thumbnail_path"]:
                from pathlib import Path
                thumb_path = Path(job["thumbnail_path"])
                if thumb_path.exists():
                    thumb_path.unlink()

            # ── STEP 5: Report result ──────────────────────────────────
            if upload_result["success"]:
                video_id = upload_result["video_id"]
                video_url = upload_result["url"]

                db.update_queue_status(queue_id, "completed")
                db.update_upload_status(
                    history_id, "completed",
                    youtube_video_id=video_id,
                    file_size=file_size
                )

                remaining = monetization.get_remaining_minutes(user_id)

                if progress_callback:
                    await progress_callback({
                        "stage": "completed",
                        "message": (
                            f"✅ **Upload Complete!**\n\n"
                            f"📹 **Title:** {upload_result['title']}\n"
                            f"🔗 **URL:** {video_url}\n"
                            f"🆔 **Video ID:** `{video_id}`\n\n"
                            f"⏱ **Remaining Time:** {remaining:.1f} minutes"
                        ),
                        "video_id": video_id,
                        "video_url": video_url
                    })

                logger.info(f"[Queue {queue_id}] Upload completed: {video_url}")

            else:
                error_msg = upload_result.get("error", "Upload failed")
                db.update_queue_status(queue_id, "failed", error_msg)
                db.update_upload_status(history_id, "failed", error_message=error_msg)

                if progress_callback:
                    await progress_callback({
                        "stage": "failed",
                        "message": f"❌ **Upload Failed**\n\n{error_msg}",
                        "error": error_msg
                    })

        except Exception as e:
            logger.error(f"[Queue {queue_id}] Unexpected error: {e}")
            db.update_queue_status(queue_id, "failed", str(e))
            db.update_upload_status(history_id, "failed", error_message=str(e))

            if progress_callback:
                await progress_callback({
                    "stage": "failed",
                    "message": f"❌ **Error**\n\n{str(e)}",
                    "error": str(e)
                })

        finally:
            self.is_processing = False
            self.current_job = None
            # Remove callback
            self._progress_callbacks.pop(user_id, None)

    def get_queue_position(self, user_id: int) -> int:
        """Get a user's position in the queue."""
        return db.get_user_queue_position(user_id)

    def get_queue_length(self) -> int:
        """Get total items in queue."""
        return db.get_queue_length()

    def get_current_job(self) -> Optional[Dict[str, Any]]:
        """Get the currently processing job."""
        return self.current_job


# Global queue instance
upload_queue = UploadQueue()
