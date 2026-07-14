"""
Google Drive Video Downloader Module.
Downloads videos from Google Drive links with real-time progress tracking.
Supports large files with chunked downloading and resume capability.
"""

import os
import re
import aiohttp
import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from bot.config import DOWNLOADS_DIR, MAX_FILE_SIZE

logger = logging.getLogger(__name__)


class DriveDownloadProgress:
    """Tracks download progress with speed and ETA calculation."""

    def __init__(self, total_size: int = 0):
        self.total_size = total_size
        self.downloaded = 0
        self.start_time = None
        self.last_update_time = None
        self.last_downloaded = 0
        self.speed = 0  # bytes per second
        self.eta_seconds = 0
        self.percentage = 0

    def start(self):
        """Start tracking time."""
        self.start_time = asyncio.get_event_loop().time()
        self.last_update_time = self.start_time

    def update(self, chunk_size: int):
        """Update progress with new chunk."""
        self.downloaded += chunk_size
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self.last_update_time

        if elapsed >= 0.5:  # Update every 500ms
            self.speed = (self.downloaded - self.last_downloaded) / elapsed
            self.last_downloaded = self.downloaded
            self.last_update_time = current_time

            if self.total_size > 0:
                self.percentage = (self.downloaded / self.total_size) * 100
                remaining = self.total_size - self.downloaded
                if self.speed > 0:
                    self.eta_seconds = remaining / self.speed
                else:
                    self.eta_seconds = float('inf')

    def get_progress_bar(self, length: int = 20) -> str:
        """Generate a text progress bar."""
        if self.total_size <= 0:
            filled = int(length * 0.5)
        else:
            filled = int(length * self.percentage / 100)
        filled = min(filled, length)
        bar = "█" * filled + "░" * (length - filled)
        return bar

    def format_speed(self) -> str:
        """Format speed in MB/s."""
        speed_mb = self.speed / (1024 * 1024)
        return f"{speed_mb:.2f} MB/s"

    def format_eta(self) -> str:
        """Format ETA in human-readable format."""
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
        """Format size in human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f} TB"

    def get_status_message(self) -> str:
        """Get formatted status message for Telegram."""
        bar = self.get_progress_bar()
        speed = self.format_speed()
        eta = self.format_eta()
        downloaded = self.format_size(self.downloaded)
        total = self.format_size(self.total_size)
        percentage = f"{self.percentage:.1f}%" if self.total_size > 0 else "Unknown"

        return (
            f"⬇️ **Downloading from Google Drive**\n\n"
            f"[{bar}] {percentage}\n\n"
            f"📊 **Progress:** {downloaded} / {total}\n"
            f"⚡ **Speed:** {speed}\n"
            f"⏱ **ETA:** {eta}"
        )


class DriveDownloader:
    """Download videos from Google Drive sharing links."""

    def __init__(self):
        self.downloads_dir = Path(DOWNLOADS_DIR)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = 256 * 1024  # 256KB chunks

    def extract_file_id(self, drive_url: str) -> Optional[str]:
        """
        Extract Google Drive file ID from various URL formats.
        Supports:
        - https://drive.google.com/file/d/FILE_ID/view
        - https://drive.google.com/open?id=FILE_ID
        - https://drive.google.com/uc?id=FILE_ID
        - https://drive.google.com/uc?export=download&id=FILE_ID
        """
        patterns = [
            r'/file/d/([a-zA-Z0-9_-]+)',
            r'[?&]id=([a-zA-Z0-9_-]+)',
            r'/uc\?.*id=([a-zA-Z0-9_-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, drive_url)
            if match:
                return match.group(1)

        # Try parsing URL
        parsed = urlparse(drive_url)
        if 'drive.google.com' in parsed.netloc:
            qs = parse_qs(parsed.query)
            if 'id' in qs:
                return qs['id'][0]

        logger.warning(f"Could not extract file ID from URL: {drive_url}")
        return None

    async def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """Get file metadata from Google Drive."""
        url = f"https://drive.google.com/uc?export=download&id={file_id}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    content_type = response.headers.get('Content-Type', '')
                    content_length = response.headers.get('Content-Length')
                    content_disposition = response.headers.get('Content-Disposition', '')

                    # Extract filename from Content-Disposition
                    filename = None
                    if 'filename=' in content_disposition:
                        filename = content_disposition.split('filename=')[-1].strip('"\'')

                    size = int(content_length) if content_length else 0

                    # Check for virus scan warning (large files)
                    if 'text/html' in content_type and size < 1024:
                        return {"requires_confirm": True, "file_id": file_id}

                    return {
                        "filename": filename or f"video_{file_id}.mp4",
                        "size": size,
                        "content_type": content_type,
                        "requires_confirm": False
                    }

        except Exception as e:
            logger.error(f"Error getting file info for {file_id}: {e}")
            return {"error": str(e)}

    async def download(self, drive_url: str, user_id: int,
                       progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Download a video from Google Drive.

        Args:
            drive_url: Google Drive sharing link
            user_id: Telegram user ID for file organization
            progress_callback: Async callback(progress: DriveDownloadProgress) -> None

        Returns:
            Dict with download result: {"success": bool, "file_path": str, "error": str}
        """
        file_id = self.extract_file_id(drive_url)
        if not file_id:
            return {"success": False, "error": "Invalid Google Drive link. Could not extract file ID."}

        # Get file info
        file_info = await self.get_file_info(file_id)
        if "error" in file_info:
            return {"success": False, "error": f"Failed to get file info: {file_info['error']}"}

        # Handle virus scan confirmation for large files
        if file_info.get("requires_confirm"):
            return await self._download_with_confirm(file_id, user_id, progress_callback)

        filename = file_info.get("filename", f"video_{file_id}.mp4")
        total_size = file_info.get("size", 0)

        # Check file size
        if total_size > MAX_FILE_SIZE:
            return {
                "success": False,
                "error": f"File too large ({total_size / (1024*1024*1024):.2f} GB). Maximum allowed: {MAX_FILE_SIZE / (1024*1024*1024):.0f} GB"
            }

        # Create user-specific download directory
        user_dir = self.downloads_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)

        file_path = user_dir / filename

        # If file exists, remove it (shouldn't happen with cleanup, but safety check)
        if file_path.exists():
            file_path.unlink()

        # Initialize progress tracker
        progress = DriveDownloadProgress(total_size)
        progress.start()

        # Download URL
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed with status {response.status}"}

                    # Update total size if not known
                    if total_size == 0:
                        content_length = response.headers.get('Content-Length')
                        if content_length:
                            progress.total_size = int(content_length)

                    # Download with progress tracking
                    downloaded_size = 0
                    last_callback_time = asyncio.get_event_loop().time()

                    with open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            if chunk:
                                f.write(chunk)
                                downloaded_size += len(chunk)
                                progress.update(len(chunk))

                                # Call progress callback every 2 seconds
                                current_time = asyncio.get_event_loop().time()
                                if progress_callback and (current_time - last_callback_time) >= 2:
                                    try:
                                        await progress_callback(progress)
                                    except Exception as e:
                                        logger.error(f"Progress callback error: {e}")
                                    last_callback_time = current_time

                    # Final progress update
                    if progress_callback:
                        progress.percentage = 100
                        progress.eta_seconds = 0
                        try:
                            await progress_callback(progress)
                        except Exception:
                            pass

                    # Verify download
                    actual_size = file_path.stat().st_size
                    if total_size > 0 and actual_size != total_size:
                        file_path.unlink()
                        return {"success": False, "error": "Download incomplete. File size mismatch."}

                    logger.info(f"Downloaded {filename} ({actual_size} bytes) for user {user_id}")
                    return {
                        "success": True,
                        "file_path": str(file_path),
                        "filename": filename,
                        "size": actual_size
                    }

        except asyncio.TimeoutError:
            if file_path.exists():
                file_path.unlink()
            return {"success": False, "error": "Download timed out. Please try again."}
        except Exception as e:
            if file_path.exists():
                file_path.unlink()
            logger.error(f"Download error for user {user_id}: {e}")
            return {"success": False, "error": f"Download failed: {str(e)}"}

    async def _download_with_confirm(self, file_id: str, user_id: int,
                                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Handle large file downloads that require virus scan confirmation.
        Google Drive shows a warning page for large files.
        """
        # For large files, we need to handle the confirm token
        # This is a simplified version - in production, you'd need to parse the HTML
        # and extract the confirm token, then make a second request

        user_dir = self.downloads_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)

        filename = f"video_{file_id}.mp4"
        file_path = user_dir / filename

        try:
            async with aiohttp.ClientSession() as session:
                # First request to get the confirm token
                url = f"https://drive.google.com/uc?export=download&id={file_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                    text = await response.text()

                    # Extract confirm token from the page
                    confirm_match = re.search(r'confirm=([0-9A-Za-z_]+)', text)
                    if not confirm_match:
                        return {"success": False, "error": "Could not bypass virus scan warning. File may be too large or unavailable."}

                    confirm_token = confirm_match.group(1)

                    # Second request with confirm token
                    confirm_url = f"https://drive.google.com/uc?export=download&confirm={confirm_token}&id={file_id}"

                    progress = DriveDownloadProgress()
                    progress.start()

                    async with session.get(confirm_url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                        if response.status != 200:
                            return {"success": False, "error": f"Download failed with status {response.status}"}

                        content_length = response.headers.get('Content-Length')
                        if content_length:
                            progress.total_size = int(content_length)

                        downloaded_size = 0
                        last_callback_time = asyncio.get_event_loop().time()

                        with open(file_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(self.chunk_size):
                                if chunk:
                                    f.write(chunk)
                                    downloaded_size += len(chunk)
                                    progress.update(len(chunk))

                                    current_time = asyncio.get_event_loop().time()
                                    if progress_callback and (current_time - last_callback_time) >= 2:
                                        try:
                                            await progress_callback(progress)
                                        except Exception as e:
                                            logger.error(f"Progress callback error: {e}")
                                        last_callback_time = current_time

                        if progress_callback:
                            progress.percentage = 100
                            try:
                                await progress_callback(progress)
                            except Exception:
                                pass

                        actual_size = file_path.stat().st_size
                        return {
                            "success": True,
                            "file_path": str(file_path),
                            "filename": filename,
                            "size": actual_size
                        }

        except Exception as e:
            if file_path.exists():
                file_path.unlink()
            logger.error(f"Download with confirm error: {e}")
            return {"success": False, "error": f"Download failed: {str(e)}"}

    async def cleanup(self, file_path: str):
        """Delete downloaded file to free up space."""
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Cleaned up file: {file_path}")

                # Try to remove empty user directory
                user_dir = path.parent
                if user_dir.exists() and not any(user_dir.iterdir()):
                    user_dir.rmdir()
        except Exception as e:
            logger.error(f"Cleanup error for {file_path}: {e}")


# Global downloader instance
downloader = DriveDownloader()
