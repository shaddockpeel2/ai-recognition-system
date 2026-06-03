"""
Frame readers used by the video source detector.

The detector consumes decoded OpenCV frames, while this module owns the
transport/decoder choice. On RK devices it can prefer a GStreamer pipeline
with MPP hardware decoding for ZLM RTSP output, and fall back to OpenCV/FFmpeg
when the pipeline is unavailable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import cv2


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_network_stream(url: str) -> bool:
    return str(url or "").lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))


@dataclass
class ReaderOptions:
    transport: str = "tcp"
    codec: str = "h264"
    prefer_hardware_decode: bool = True
    allow_fallback: bool = True


class OpenCVFrameReader:
    """Low-latency OpenCV/FFmpeg reader used as the compatibility path."""

    def __init__(self, url: str, options: Optional[ReaderOptions] = None):
        self.url = url
        self.options = options or ReaderOptions()
        self.capture = None

    def open(self) -> bool:
        previous_ffmpeg_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        try:
            if _is_network_stream(self.url):
                if self.url.lower().startswith("rtmp://"):
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                        "rtmp_live;live|fflags;nobuffer|flags;low_delay|max_delay;500000"
                    )
                else:
                    rtsp_transport = "udp" if self.options.transport.lower() == "udp" else "tcp"
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                        f"rtsp_transport;{rtsp_transport}|fflags;nobuffer|flags;low_delay|max_delay;500000"
                    )
                self.capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            else:
                self.capture = cv2.VideoCapture(self.url)

            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return self.is_opened()
        finally:
            if previous_ffmpeg_options is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = previous_ffmpeg_options

    def is_opened(self) -> bool:
        return bool(self.capture and self.capture.isOpened())

    def read(self):
        if not self.capture:
            return False, None
        return self.capture.read()

    def read_latest(self):
        if not self.capture:
            return False, None

        for _ in range(8):
            if not self.capture.grab():
                break
        return self.capture.read()

    def get(self, prop_id):
        if not self.capture:
            return 0
        return self.capture.get(prop_id)

    def set(self, prop_id, value):
        if self.capture:
            return self.capture.set(prop_id, value)
        return False

    def release(self) -> None:
        if self.capture:
            self.capture.release()
            self.capture = None


class GStreamerMppFrameReader(OpenCVFrameReader):
    """RTSP reader using GStreamer MPP hardware decode on Rockchip devices."""

    def _build_pipeline(self) -> str:
        codec = (self.options.codec or "h264").strip().lower()
        transport = "udp" if self.options.transport.lower() == "udp" else "tcp"
        latency = int(os.getenv("SAFEHAT_GST_RTSP_LATENCY", "80") or 80)

        if codec in {"h265", "hevc", "265"}:
            depay = "rtph265depay"
            parser = "h265parse"
        else:
            depay = "rtph264depay"
            parser = "h264parse"

        return (
            f'rtspsrc location="{self.url}" protocols={transport} latency={latency} drop-on-latency=true '
            f"! {depay} ! {parser} ! mppvideodec "
            "! videoconvert ! video/x-raw,format=BGR "
            "! appsink drop=true max-buffers=1 sync=false"
        )

    def open(self) -> bool:
        pipeline = self._build_pipeline()
        self.capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if self.is_opened():
            print(f"[FrameReader] GStreamer MPP decode enabled: codec={self.options.codec}, url={self.url}")
            return True
        self.release()
        return False

    def read_latest(self):
        return self.read()


def create_frame_reader(url: str, options: Optional[ReaderOptions] = None):
    """Create and open the best available reader for a URL."""
    options = options or ReaderOptions()
    use_hardware = options.prefer_hardware_decode and url.lower().startswith("rtsp://")

    if use_hardware:
        reader = GStreamerMppFrameReader(url, options)
        if reader.open():
            return reader
        if not options.allow_fallback:
            return reader
        print(f"[FrameReader] GStreamer MPP decode unavailable, falling back to OpenCV: {url}")

    reader = OpenCVFrameReader(url, options)
    reader.open()
    return reader
