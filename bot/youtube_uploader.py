"""
YouTube Upload Engine Module.
Handles video uploads to YouTube with resumable upload support,
progress tracking, auto-retry, and thumbnail application.
"""

import os
import asyncio
import logging
import random
from typing import Optional, Callable, Dict, Any
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

from bot.config import UPLOAD_CHUNK_SIZE, MAX_RETRIES
from bot.oauth_handler import oauth_handler

logger = logging.getLogger(__name__)

# Video categories mapping
VIDEO_CATEGORIES = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "18": "Short Movies",
    "19": "Travel & Events",
    "20": "Gaming",
    "21": "Videoblogging",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
    "30": "Movies",
    "31": "Anime/Animation",
    "32": "Action/Adventure",
    "33": "Classics",
    "34": "Comedy",
    "35": "Documentary",
    "36": "Drama",
    "37": "Family",
    "38": "Foreign",
    "39": "Horror",
    "40": "Sci-Fi/Fantasy",
    "41": "Thriller",
    "42": "Shorts",
    "43": "Shows",
    "44": "Trailers"
}


class UploadProgress:
    """Tracks YouTube upload progress with speed and ETA."""

    def __init__(self, total_size: int = 0):
        self.total_size = total_size
        self.uploaded = 0
        self.start_time = None
        self.last_update_time = None
        self.last_uploaded = 0
        self.speed = 0
        self.eta_seconds = 0
        self.percentage = 0
        self.status = "preparing"  # preparing, uploading, processing, completed, failed
        self.stage = "Initializing upload..."

    def start(self):
        """Start tracking time."""
        self.start_time = asyncio.get_event_loop().time()
        self.last_update_time = self.start_time
        self.status = "uploading"
        self.stage = "Uploading to YouTube..."

    def update(self, uploaded_bytes: int):
        """Update progress."""
        self.uploaded = uploaded_bytes
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self.last_update_time

        if elapsed >= 0.5:
            self.speed = (self.uploaded - self.last_uploaded) / elapsed
            self.last_uploaded = self.uploaded
            self.last_update_time = current_time

            if self.total_size > 0:
                self.percentage = (self.uploaded / self.total_size) * 100
                remaining = self.total_size - self.uploaded
                if self.speed > 0:
                    self.eta_seconds = remaining / self.speed
                else:
                    self.eta_seconds = float('inf')

    def get_progress_bar(self, length: int = 20) -> str:
        """Generate a text progress bar."""
        if self.total_size <= 0:
            filled = 0
        else:
            filled = int(length * self.percentage / 100)
        filled = min(filled, length)
        return "█" * filled + "░" * (length - filled)

    def format_speed(self) -> str:
        """Format speed in MB/s."""
        speed_mb = self.speed / (1024 * 1024)
        return f"{speed_mb:.2f} MB/s"

    def format_eta(self) -> str:
        """Format ETA."""
        if self.eta_seconds == float('inf'):
            return "Calculating..."
        eta = int(self.eta_seconds)
        minutes, seconds = divmod(eta, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def format_size(self, size_bytes: int) -> str:
        """Format size."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} TB"

    def get_status_message(self) -> str:
        """Get formatted status message."""
        bar = self.get_progress_bar()
        speed = self.format_speed()
        eta = self.format_eta()
        uploaded = self.format_size(self.uploaded)
        total = self.format_size(self.total_size)
        percentage = f"{self.percentage:.1f}%" if self.total_size > 0 else "0%"

        status_emoji = {
            "preparing": "🔄",
            "uploading": "⬆️",
            "processing": "⚙️",
            "completed": "✅",
            "failed": "❌"
        }.get(self.status, "⬆️")

        return (
            f"{status_emoji} **Uploading to YouTube**\n\n"
            f"📋 **Stage:** {self.stage}\n"
            f"[{bar}] {percentage}\n\n"
            f"📊 **Progress:** {uploaded} / {total}\n"
            f"⚡ **Speed:** {speed}\n"
            f"⏱ **ETA:** {eta}"
        )


class YouTubeUploader:
    """Handle video uploads to YouTube with resumable upload support."""

    def __init__(self):
        self.chunk_size = UPLOAD_CHUNK_SIZE
        self.max_retries = MAX_RETRIES

    def _get_media_body(self, file_path: str):
        """Create a MediaFileUpload object with proper chunking."""
        file_size = os.path.getsize(file_path)
        # For files > 5MB, use resumable upload
        if file_size > 5 * 1024 * 1024:
            return MediaFileUpload(
                file_path,
                chunksize=self.chunk_size,
                resumable=True
            )
        else:
            return MediaFileUpload(file_path)

    def _build_video_body(self, title: str, description: str = None,
                          tags: str = None, category_id: str = "22",
                          privacy_status: str = "private") -> Dict[str, Any]:
        """Build the video resource body for the API request."""
        body = {
            "snippet": {
                "title": title,
                "description": description or "",
                "tags": [],
                "categoryId": category_id or "22"
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        # Parse tags
        if tags:
            tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
            body["snippet"]["tags"] = tag_list

        return body

    async def upload_video(self, user_id: int, file_path: str,
                           title: str, description: str = None,
                           tags: str = None, category_id: str = "22",
                           privacy_status: str = "private",
                           thumbnail_path: str = None,
                           progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Upload a video to YouTube with progress tracking and retry logic.

        Args:
            user_id: Telegram user ID
            file_path: Local path to video file
            title: Video title
            description: Video description
            tags: Comma-separated tags
            category_id: YouTube category ID
            privacy_status: 'public', 'private', or 'unlisted'
            thumbnail_path: Path to thumbnail image
            progress_callback: Async callback for progress updates

        Returns:
            Dict with upload result
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return {"success": False, "error": "Video file not found"}

        # Get authenticated YouTube service
        youtube = await oauth_handler.get_youtube_service(user_id)
        if not youtube:
            return {"success": False, "error": "YouTube authentication required. Please login with /login"}

        file_size = file_path.stat().st_size
        progress = UploadProgress(file_size)

        # Build video metadata
        video_body = self._build_video_body(
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy_status
        )

        media_body = self._get_media_body(str(file_path))

        # Attempt upload with retries
        for attempt in range(1, self.max_retries + 1):
            try:
                progress.start()

                # Create the insert request
                loop = asyncio.get_event_loop()
                insert_request = await loop.run_in_executor(
                    None,
                    lambda: youtube.videos().insert(
                        part=",".join(video_body.keys()),
                        body=video_body,
                        media_body=media_body
                    )
                )

                # Execute resumable upload with progress tracking
                video_id = None
                response = None

                while response is None:
                    status, response = await loop.run_in_executor(
                        None,
                        insert_request.next_chunk
                    )

                    if status:
                        progress.update(status.resumable_progress)

                        # Call progress callback
                        if progress_callback:
                            try:
                                await progress_callback(progress)
                            except Exception as e:
                                logger.error(f"Progress callback error: {e}")

                # Upload successful
                video_id = response.get("id")
                progress.status = "completed"
                progress.percentage = 100
                progress.stage = "Upload complete!"

                if progress_callback:
                    try:
                        await progress_callback(progress)
                    except Exception:
                        pass

                logger.info(f"Video uploaded successfully: {video_id}")

                # Upload thumbnail if provided
                if thumbnail_path and Path(thumbnail_path).exists():
                    await self._upload_thumbnail(youtube, video_id, thumbnail_path)

                return {
                    "success": True,
                    "video_id": video_id,
                    "title": response.get("snippet", {}).get("title", title),
                    "url": f"https://youtube.com/watch?v={video_id}"
                }

            except HttpError as e:
                error_details = e.error_details if hasattr(e, 'error_details') else str(e)
                logger.error(f"Upload attempt {attempt} failed for user {user_id}: {error_details}")

                if attempt < self.max_retries:
                    wait_time = random.uniform(2, 5) * attempt  # Exponential backoff
                    progress.stage = f"Retrying in {wait_time:.0f}s (attempt {attempt + 1}/{self.max_retries})..."
                    if progress_callback:
                        try:
                            await progress_callback(progress)
                        except Exception:
                            pass
                    await asyncio.sleep(wait_time)

                    # Reset media body for retry
                    media_body = self._get_media_body(str(file_path))
                else:
                    progress.status = "failed"
                    progress.stage = "Upload failed"
                    if progress_callback:
                        try:
                            await progress_callback(progress)
                        except Exception:
                            pass
                    return {"success": False, "error": f"Upload failed after {self.max_retries} attempts: {error_details}"}

            except Exception as e:
                logger.error(f"Unexpected upload error for user {user_id}: {e}")
                progress.status = "failed"
                if progress_callback:
                    try:
                        await progress_callback(progress)
                    except Exception:
                        pass
                return {"success": False, "error": f"Upload error: {str(e)}"}

        return {"success": False, "error": "Upload failed"}

    async def _upload_thumbnail(self, youtube, video_id: str, thumbnail_path: str):
        """Upload a custom thumbnail for the video."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path)
                ).execute()
            )
            logger.info(f"Thumbnail uploaded for video {video_id}")
            return True
        except Exception as e:
            logger.error(f"Error uploading thumbnail for {video_id}: {e}")
            return False

    async def check_upload_quota(self, user_id: int) -> Dict[str, Any]:
        """Check remaining YouTube upload quota for a user."""
        try:
            youtube = await oauth_handler.get_youtube_service(user_id)
            if not youtube:
                return {"available": False, "error": "Not authenticated"}

            # Get quota info (this is a simplified check)
            # In production, you'd track quota usage yourself as Google doesn't provide a direct API
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: youtube.channels().list(part="snippet,statistics", mine=True).execute()
            )

            return {"available": True}

        except HttpError as e:
            if e.resp.status == 403:
                return {"available": False, "error": "YouTube quota exceeded"}
            return {"available": False, "error": str(e)}
        except Exception as e:
            return {"available": False, "error": str(e)}

    @staticmethod
    def get_category_list() -> str:
        """Get formatted list of YouTube video categories."""
        lines = ["📋 **Available YouTube Categories:**\n"]
        for cat_id, cat_name in VIDEO_CATEGORIES.items():
            lines.append(f"`{cat_id}` - {cat_name}")
        return "\n".join(lines)

    @staticmethod
    def validate_category(category_id: str) -> bool:
        """Validate a YouTube category ID."""
        return category_id in VIDEO_CATEGORIES


# Global uploader instance
uploader = YouTubeUploader()
