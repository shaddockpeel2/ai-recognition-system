"""
Processed-frame pushers for publishing algorithm output back to ZLMediaKit.

The current production path publishes H264 over RTMP because ZLM accepts RTMP
publish reliably and can fan it out as WebRTC/FLV/RTSP for browsers and tools.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _falsey(value: object) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


@dataclass
class PushOptions:
    enabled: bool = True
    output_url: str = ""
    fps: float = 20.0
    width: int = 0
    height: int = 0
    prefer_hardware_encode: bool = True
    bitrate: int = 2500


class ProcessedFramePusher:
    """Push processed BGR frames into ZLM through a GStreamer appsrc pipeline."""

    def __init__(self, options: PushOptions):
        self.options = options
        self.writer = None
        self.frame_size: Optional[Tuple[int, int]] = None
        self.failed = False

    def open(self, frame) -> bool:
        if not self.options.enabled or not self.options.output_url or frame is None:
            return False

        height, width = frame.shape[:2]
        if self.options.width > 0 and self.options.height > 0:
            width, height = self.options.width, self.options.height

        self.frame_size = (width, height)
        pipeline = self._build_pipeline(width, height)
        self.writer = cv2.VideoWriter(pipeline, cv2.CAP_GSTREAMER, 0, self.options.fps, (width, height), True)
        if self.writer.isOpened():
            print(f"[FramePusher] publishing processed stream: {self.options.output_url}")
            return True

        self.release()
        self.failed = True
        print(f"[FramePusher] failed to open processed stream publisher: {self.options.output_url}")
        return False

    def _build_pipeline(self, width: int, height: int) -> str:
        fps = max(1, int(round(self.options.fps or 20)))
        bitrate = max(256, int(self.options.bitrate or 2500))

        if self.options.prefer_hardware_encode:
            encoder = f"mpph264enc bps={bitrate * 1000} gop={fps * 2}"
        else:
            encoder = f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} key-int-max={fps * 2}"

        return (
            "appsrc is-live=true block=false format=time do-timestamp=true "
            f"! video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1 "
            "! queue leaky=downstream max-size-buffers=2 "
            "! videoconvert "
            f"! {encoder} "
            "! h264parse config-interval=1 "
            "! flvmux streamable=true "
            f'! rtmpsink location="{self.options.output_url}" sync=false async=false'
        )

    def write(self, frame) -> bool:
        if self.failed or not self.options.enabled:
            return False

        if self.writer is None:
            if not self.open(frame):
                return False

        height, width = frame.shape[:2]
        if self.frame_size and (width, height) != self.frame_size:
            frame = self._letterbox_to_frame_size(frame, self.frame_size)

        try:
            self.writer.write(frame)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[FramePusher] write failed: {exc}")
            self.failed = True
            self.release()
            return False

    @staticmethod
    def _letterbox_to_frame_size(frame, target_size: Tuple[int, int]):
        """Fit frame into target size without cropping or aspect-ratio distortion."""
        target_width, target_height = target_size
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0 or target_width <= 0 or target_height <= 0:
            return frame

        scale = min(target_width / width, target_height / height)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

        canvas = np.zeros((target_height, target_width, 3), dtype=frame.dtype)
        x = (target_width - resized_width) // 2
        y = (target_height - resized_height) // 2
        canvas[y:y + resized_height, x:x + resized_width] = resized
        return canvas

    def release(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None


def create_processed_frame_pusher(source_id: int, source_config: dict):
    """Create a pusher for a source using env/config defaults."""
    source_config = source_config or {}
    # Publishing processed frames is heavier and depends on ZLM RTMP being up.
    # Keep it opt-in so a failed publish pipeline cannot destabilize detection.
    enabled_value = source_config.get("push_processed_stream", os.getenv("SAFEHAT_PUSH_PROCESSED_ENABLED", "0"))
    enabled = _truthy(enabled_value) and not _falsey(enabled_value)

    app = str(
        source_config.get("processed_gateway_app")
        or os.getenv("SAFEHAT_ZLM_RESULT_APP", "safehat_result")
    ).strip() or "safehat_result"
    stream = str(
        source_config.get("processed_gateway_stream")
        or f"source_{source_id}"
    ).strip() or f"source_{source_id}"

    output_url = str(source_config.get("processed_output_url") or "").strip()
    if not output_url:
        host = str(os.getenv("SAFEHAT_ZLM_PUSH_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(os.getenv("SAFEHAT_ZLM_RTMP_PORT", "1935") or 1935)
        output_url = f"rtmp://{host}:{port}/{app}/{stream}"

    prefer_hardware = not _falsey(
        source_config.get("hardware_encode", os.getenv("SAFEHAT_MPP_ENCODE_ENABLED", "1"))
    )
    fps = float(source_config.get("processed_fps") or os.getenv("SAFEHAT_PROCESSED_STREAM_FPS", "20") or 20)
    bitrate = int(source_config.get("processed_bitrate") or os.getenv("SAFEHAT_PROCESSED_STREAM_BITRATE", "2500") or 2500)

    return ProcessedFramePusher(
        PushOptions(
            enabled=enabled,
            output_url=output_url,
            fps=fps,
            prefer_hardware_encode=prefer_hardware,
            bitrate=bitrate,
        )
    )
