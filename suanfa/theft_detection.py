"""
鍋风洍妫€娴嬶紙绗竴鐗堬級

褰撳墠鐗堟湰鍏堝鐢ㄤ汉鍛樻娴嬭兘鍔涳細
- 妫€娴嬪埌 person(class_id=0)
- 灏辨爣璁颁负涓€娆″伔鐩楁娴嬪懡涓?"""

import cv2
import numpy as np
import json
import os
import re
import time
import base64
import tempfile
import subprocess
import selectors
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError
from pathlib import Path
from typing import Optional, Dict

from .base_algorithm import BaseAlgorithm

LOCAL_DEMO_CONFIG = {
    "demo_path": "/home/jhc/qwen2vl/demo_Linux_aarch64/demo",
    "vision_model": "/home/jhc/qwen2vl/qwen2_vl_2b_vision_rk3576.rknn",
    "llm_model": "/home/jhc/qwen2vl/qwen2-vl-2b-instruct_w4a16_rk3576.rkllm",
    "cwd": "/home/jhc/qwen2vl/demo_Linux_aarch64",
    "lib_dir": "/home/jhc/qwen2vl/demo_Linux_aarch64/lib",
    "img_h": 1024,
    "img_w": 2048,
    "core_num": 2,
    "vision_start": "<|vision_start|>",
    "vision_end": "<|vision_end|>",
    "image_pad": "<|image_pad|>",
    "infer_wait": 4,
    "prompt": (
        "<image>"
        "请根据图片判断是否存在偷盗行为。"
        "如果存在，请简要说明判定原因。"
        "请严格按如下格式输出："
        "结论：有 或 结论：没有；"
        "原因：xxx"
    ),
}


class TheftDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5):
        self.person_class_id = 0
        self.class_config = {
            0: {
                "name": "偷盗检测",
                "color": (0, 0, 255),
                "status": "疑似偷盗",
                "level": "critical",
            },
        }
        super().__init__(confidence_threshold)
        self.detection_count = {"person": 0}

    def get_algorithm_name(self) -> str:
        return "theft_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "浜哄憳璇嗗埆" / "person.engine")

    def enable(self):
        super().enable()
        self.detection_count = {"person": 0}

    def reset_count(self):
        self.detection_count = {"person": 0}

    def detect(self, frame: np.ndarray):
        if not self.is_enabled:
            return frame, [], {"person": 0}, []

        if not self._validate_frame(frame):
            return frame, [], {"person": 0}, []

        try:
            raw_dets = self._infer(frame)
            detection_info = []
            frame_detections = {"person": 0}
            processed_frame = frame.copy()
            theft_alerts = []

            for det in raw_dets:
                if det["class_id"] != self.person_class_id:
                    continue
                if not self._validate_bbox(det["bbox"], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det["bbox"], frame.shape)
                conf = det["confidence"]

                processed_frame = self._draw_detection_box(processed_frame, x1, y1, x2, y2, conf)
                frame_detections["person"] += 1
                detection_info.append(f"[!] THEFT: {conf:.1%}")
                theft_alerts.append({
                    "bbox": (x1, y1, x2, y2),
                    "confidence": conf,
                    "message": "检测到人员，标记为偷盗检测命中",
                    "status": "疑似偷盗",
                })

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

            return processed_frame, detection_info, frame_detections, theft_alerts
        except Exception as e:
            print(f"[{self.algorithm_name}] 妫€娴嬮敊璇? {e}")
            import traceback
            traceback.print_exc()
            return frame, [], {"person": 0}, []

    def _draw_detection_box(self, frame, x1, y1, x2, y2, conf):
        color = self.class_config[0]["color"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

        label = f"[!] THEFT: {conf:.1%}"
        text_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        bg_y1 = max(0, y1 - text_size[1] - 18)
        cv2.rectangle(frame, (x1, bg_y1), (x1 + text_size[0] + 18, y1), color, -1)
        cv2.putText(frame, label, (x1 + 8, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return frame

    def judge_with_llm(self, cache_payload: dict, endpoint: str = "", timeout: int = 20, extra: Optional[Dict] = None):
        """
        鍩轰簬缂撳瓨鐨?0甯ц皟鐢ㄥ閮ㄥぇ妯″瀷鏈嶅姟杩涜鍋风洍鍒ゅ畾銆?        璇存槑锛?        - endpoint 涓虹┖鏃惰繑鍥炲崰浣嶇粨鏋滐紝鏂逛究鍏堣仈璋冩帴鍙ｃ€?        - endpoint 涓嶄负绌烘椂锛屽悜璇ュ湴鍧€鍙戦€?POST JSON 璇锋眰銆?        """
        cache_payload = cache_payload or {}
        frames = cache_payload.get("frames") or []
        # 缁熶竴璇锋眰浣擄細涓嶇粦瀹氬叿浣撳巶鍟嗭紝渚夸簬鍚庣画鎺ヤ换鎰忓ぇ妯″瀷鏈嶅姟
        payload = {
            "task": "theft_judgement",
            "algorithm": self.get_algorithm_name(),
            "source_id": cache_payload.get("source_id"),
            "channel_name": cache_payload.get("channel_name"),
            "location": cache_payload.get("location"),
            "person_count": cache_payload.get("person_count"),
            "confidence": cache_payload.get("confidence"),
            "cached_at": cache_payload.get("cached_at"),
            "frame_count": len(frames),
            "frames": frames,
            "extra": extra or {},
        }

        extra_cfg = extra or {}
        local_demo_path = str(extra_cfg.get("demo_path") or LOCAL_DEMO_CONFIG["demo_path"]).strip()
        print(
            f"[theft_llm] judge start: mode={'local_demo' if (not endpoint and local_demo_path) else ('remote' if endpoint else 'mock')}, "
            f"source_id={cache_payload.get('source_id')}, frame_count={len(frames)}, timeout={timeout}"
        )

        # 默认直接走本地 demo；只有显式传 endpoint 时才走 HTTP
        if not endpoint and local_demo_path:
            local_result = self._judge_with_local_demo(cache_payload=cache_payload, timeout=timeout, extra=extra_cfg)
            local_result.setdefault("request_payload", payload)
            return local_result

        # 仅当本地 demo 配置失效时才回退到 mock
        if not endpoint:
            return {
                "success": True,
                "mode": "mock",
                "is_theft": True,
                "score": 0.99,
                "reason": "default mock result: theft detected",
                "request_payload": payload,
            }

        try:
            # 杩滅▼璋冪敤绾﹀畾锛欻TTP POST + JSON
            req = urllib_request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                parsed = json.loads(body) if body else {}
            parsed_data = parsed.get("data") if isinstance(parsed, dict) else None
            is_theft = None
            score = None
            reason = ""
            if isinstance(parsed, dict):
                is_theft = parsed.get("is_theft")
                score = parsed.get("score")
                reason = parsed.get("reason", "")
            if is_theft is None and isinstance(parsed_data, dict):
                is_theft = parsed_data.get("is_theft")
                score = parsed_data.get("score", score)
                reason = parsed_data.get("reason", reason)
            return {
                "success": True,
                "mode": "remote",
                "endpoint": endpoint,
                "is_theft": is_theft,
                "score": score,
                "reason": reason,
                "response": parsed,
            }
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            return {
                "success": False,
                "mode": "remote",
                "endpoint": endpoint,
                "error": f"http_error_{e.code}",
                "detail": body or str(e),
            }
        except URLError as e:
            return {
                "success": False,
                "mode": "remote",
                "endpoint": endpoint,
                "error": "url_error",
                "detail": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "mode": "remote",
                "endpoint": endpoint,
                "error": "unexpected_error",
                "detail": str(e),
            }

    def _judge_with_local_demo(self, cache_payload: dict, timeout: int, extra: Dict):
        """
        浣跨敤鏈湴 qwen2vl demo 閫愬抚鍒ゅ畾锛屽啀鍋氬鏁版姇绁ㄣ€?        閰嶇疆鏉ユ簮锛?        - extra
        - 鐜鍙橀噺锛圱HEFT_LOCAL_*锛?        """
        frames = cache_payload.get("frames") or []
        if not frames:
            return {
                "success": False,
                "mode": "local_demo",
                "error": "empty_frames",
                "detail": "cache frames is empty",
            }

        temp_files = []
        print(
            f"[theft_llm] local demo batch start: source_id={cache_payload.get('source_id')}, "
            f"channel={cache_payload.get('channel_name')}, frames={len(frames[-10:])}"
        )

        try:
            montage_path = self._dump_temp_montage_image(frames[-10:])
            temp_files.append(montage_path)
            print(f"[theft_llm] montage image ready: {montage_path}")
            result = self._run_local_demo_once(montage_path, timeout=timeout, extra=extra)
            print(
                f"[theft_llm] montage result: success={result.get('success')}, "
                f"is_theft={result.get('is_theft')}, score={result.get('score')}, "
                f"reason={result.get('reason') or result.get('detail') or result.get('error', '')}"
            )

            if not result.get("success"):
                return {
                    "success": False,
                    "mode": "local_demo",
                    "error": result.get("error", "local_demo_failed"),
                    "detail": result.get("detail") or result.get("reason") or "montage inference failed",
                    "frame_results": [result],
                }

            print(
                f"[theft_llm] local demo batch done: "
                f"is_theft={result.get('is_theft')}, score={result.get('score')}"
            )
            return {
                "success": True,
                "mode": "local_demo",
                "is_theft": result.get("is_theft"),
                "score": result.get("score"),
                "reason": result.get("reason") or result.get("detail") or "",
                "frame_results": [result],
            }
        finally:
            for path in temp_files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _extract_image_base64(self, image_data_url: Optional[str]) -> str:
        if not image_data_url or not isinstance(image_data_url, str):
            return ""
        if "," in image_data_url:
            return image_data_url.split(",", 1)[1].strip()
        return image_data_url.strip()

    def _dump_temp_image(self, image_b64: str) -> str:
        raw = base64.b64decode(image_b64)
        fd, temp_path = tempfile.mkstemp(prefix="theft_frame_", suffix=".jpg")
        os.close(fd)
        with open(temp_path, "wb") as f:
            f.write(raw)
        return temp_path

    def _dump_temp_montage_image(self, frame_items) -> str:
        decoded_frames = []
        for idx, frame_item in enumerate(list(frame_items or [])[-10:]):
            image_data_url = frame_item.get("image") if isinstance(frame_item, dict) else None
            image_b64 = self._extract_image_base64(image_data_url)
            if not image_b64:
                print(f"[theft_llm] frame[{idx}] invalid image payload when building montage")
                continue
            try:
                raw = base64.b64decode(image_b64)
                arr = np.frombuffer(raw, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    decoded_frames.append(frame)
            except Exception as e:
                print(f"[theft_llm] frame[{idx}] decode failed: {e}")

        if not decoded_frames:
            raise ValueError("no valid frames for montage")

        cell_w = 320
        cell_h = 180
        cols = 5
        rows = 2
        montage = np.zeros((rows * cell_h, cols * cell_w, 3), dtype=np.uint8)

        for idx, frame in enumerate(decoded_frames[: rows * cols]):
            resized = cv2.resize(frame, (cell_w, cell_h))
            row = idx // cols
            col = idx % cols
            y1 = row * cell_h
            y2 = y1 + cell_h
            x1 = col * cell_w
            x2 = x1 + cell_w
            montage[y1:y2, x1:x2] = resized
            cv2.putText(
                montage,
                f"F{idx + 1}",
                (x1 + 10, y1 + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),
                2,
            )

        fd, temp_path = tempfile.mkstemp(prefix="theft_montage_", suffix=".jpg")
        os.close(fd)
        if not cv2.imwrite(temp_path, montage):
            raise RuntimeError("failed to write montage image")
        return temp_path

    def _is_local_demo_answer_ready(self, text: str) -> bool:
        if not text:
            return False
        normalized = re.sub(r"\s+", "", text).replace("偷盜", "偷盗")
        if "结论：" in text or "结论:" in text:
            return True
        if "原因：" in text or "原因:" in text:
            return True
        if "有偷盗" in normalized or "没有偷盗" in normalized or "偷盗行为" in normalized:
            return True
        return False

    def _run_local_demo_once(self, image_path: str, timeout: int, extra: Dict):
        demo_path = extra.get("demo_path") or LOCAL_DEMO_CONFIG["demo_path"]
        vision_model = extra.get("vision_model") or LOCAL_DEMO_CONFIG["vision_model"]
        llm_model = extra.get("llm_model") or LOCAL_DEMO_CONFIG["llm_model"]
        cwd = extra.get("cwd") or LOCAL_DEMO_CONFIG["cwd"]
        lib_dir = extra.get("lib_dir") or LOCAL_DEMO_CONFIG["lib_dir"]
        img_h = str(extra.get("img_h") or LOCAL_DEMO_CONFIG["img_h"])
        img_w = str(extra.get("img_w") or LOCAL_DEMO_CONFIG["img_w"])
        core_num = str(extra.get("core_num") or LOCAL_DEMO_CONFIG["core_num"])
        vision_start = extra.get("vision_start") or LOCAL_DEMO_CONFIG["vision_start"]
        vision_end = extra.get("vision_end") or LOCAL_DEMO_CONFIG["vision_end"]
        image_pad = extra.get("image_pad") or LOCAL_DEMO_CONFIG["image_pad"]
        infer_wait = float(extra.get("infer_wait", LOCAL_DEMO_CONFIG["infer_wait"]))
        prompt = extra.get("prompt") or LOCAL_DEMO_CONFIG["prompt"]
        cmd = [
            demo_path,
            image_path,
            vision_model,
            llm_model,
            img_h,
            img_w,
            core_num,
            vision_start,
            vision_end,
            image_pad,
        ]

        env = os.environ.copy()
        old_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = lib_dir + (":" + old_ld if old_ld else "")

        proc = None
        selector = None
        try:
            print(f"[theft_llm] start local demo process: image={image_path}")
            print(f"[theft_llm] demo_path={demo_path}")
            print(f"[theft_llm] vision_model={vision_model}")
            print(f"[theft_llm] llm_model={llm_model}")
            print(f"[theft_llm] cwd={cwd}")
            print(f"[theft_llm] infer_wait={infer_wait}, timeout={timeout}")
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            if proc.stdin:
                print("[theft_llm] local demo started, sending prompt")
                proc.stdin.write(prompt + "\n")
                proc.stdin.flush()

            wait_seconds = float(timeout)
            print(f"[theft_llm] waiting local demo output until answer or timeout: {wait_seconds:.1f}s")
            output_chunks = []
            selector = selectors.DefaultSelector()
            if proc.stdout:
                selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.time() + wait_seconds
            answer_ready = False

            while time.time() < deadline:
                if proc.poll() is not None and not selector.get_map():
                    break
                events = selector.select(timeout=0.5)
                if not events:
                    continue
                for key, _ in events:
                    line = key.fileobj.readline()
                    if not line:
                        try:
                            selector.unregister(key.fileobj)
                        except Exception:
                            pass
                        continue
                    line = line.rstrip("\r\n")
                    output_chunks.append(line)
                    print(f"[theft_llm] local demo stdout: {line}")
                    if self._is_local_demo_answer_ready("\n".join(output_chunks)):
                        answer_ready = True
                        break
                if answer_ready:
                    break

            print("[theft_llm] stopping local demo process and collecting output")
            if proc.poll() is None:
                proc.terminate()
            output = "\n".join(output_chunks)
            try:
                tail_output, _ = proc.communicate(timeout=3)
                if tail_output:
                    output = (output + "\n" + tail_output).strip()
            except Exception:
                if proc.stdout:
                    tail_output = proc.stdout.read() or ""
                    if tail_output:
                        output = (output + "\n" + tail_output).strip()

            clean = output.strip()
            print(f"[theft_llm] local demo raw output length={len(clean)}")
            compact = re.sub(r"\s+", "", clean)
            normalized = compact.replace("偷盜", "偷盗")

            verdict_match = re.search(r"结论[:：]\s*(有|没有)", clean)
            if verdict_match:
                verdict = verdict_match.group(1)
                is_theft = verdict == "有"
            elif "结论：没有" in normalized or "结论:没有" in normalized or "没有偷盗" in normalized:
                is_theft = False
            elif "结论：有" in normalized or "结论:有" in normalized or "有偷盗" in normalized or "偷盗行为" in normalized:
                is_theft = True
            else:
                is_theft = None

            reason_match = re.search(r"原因[:：]\s*(.+)", clean)
            parsed_reason = reason_match.group(1).strip() if reason_match else ""
            if not parsed_reason:
                lines = [line.strip() for line in clean.splitlines() if line.strip()]
                filtered = [
                    line for line in lines
                    if "开始大模型判断" not in line and not line.startswith("结论")
                ]
                parsed_reason = filtered[-1] if filtered else ""
            if not parsed_reason:
                parsed_reason = clean[-300:]
            print(
                f"[theft_llm] local demo parsed: is_theft={is_theft}, "
                f"reason={parsed_reason}"
            )

            return {
                "success": True,
                "is_theft": is_theft,
                "score": 1.0 if is_theft is True else (0.0 if is_theft is False else None),
                "reason": parsed_reason,
                "output": clean[-1000:],
            }
        except Exception as e:
            print(f"[theft_llm] local demo error: {e}")
            return {
                "success": False,
                "error": "local_demo_error",
                "detail": str(e),
            }
        finally:
            if selector is not None:
                try:
                    selector.close()
                except Exception:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            print("[theft_llm] local demo process cleanup complete")

    def get_status_info(self) -> dict:
        return {
            "name": "偷盗检测",
            "enabled": self.is_enabled,
            "count": self.detection_count["person"],
            "details": self.detection_count,
            "emoji": "馃毃",
        }
