"""Standalone webcam drowsiness prototype."""

from __future__ import annotations

import argparse
import urllib.request
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode


LEFT_EYE = (33, 160, 158, 133, 153, 144)
RIGHT_EYE = (362, 385, 387, 263, 373, 380)


def eye_aspect_ratio(eye_points: np.ndarray) -> float:
	"""Calculate the eye aspect ratio from six ordered eye landmarks."""
	vertical_one = np.linalg.norm(eye_points[1] - eye_points[5])
	vertical_two = np.linalg.norm(eye_points[2] - eye_points[4])
	horizontal = np.linalg.norm(eye_points[0] - eye_points[3])
	if horizontal == 0:
		return 0.0
	return float((vertical_one + vertical_two) / (2.0 * horizontal))


def landmarks_to_points(
	face_landmarks: Any,
	indices: Sequence[int],
	frame_width: int,
	frame_height: int,
) -> np.ndarray:
	landmarks = face_landmarks.landmark
	return np.array(
		[
			(landmarks[index].x * frame_width, landmarks[index].y * frame_height)
			for index in indices
		],
		dtype=np.float32,
	)


@dataclass
class ClosureTracker:
	threshold_seconds: float = 2.0
	closed_since: float | None = None

	def update(self, eyes_closed: bool, now: float | None = None) -> float:
		current_time = time.monotonic() if now is None else now
		if not eyes_closed:
			self.closed_since = None
			return 0.0
		if self.closed_since is None:
			self.closed_since = current_time
		return max(0.0, current_time - self.closed_since)

	def alert_active(self, closure_duration: float) -> bool:
		return closure_duration >= self.threshold_seconds


def play_alert() -> None:
	try:
		import winsound

		winsound.Beep(1000, 350)
	except (ImportError, RuntimeError):
		print("ALERT: prolonged eye closure detected")


def ensure_model(model_path: Path) -> None:
	if model_path.exists():
		return
	model_path.parent.mkdir(parents=True, exist_ok=True)
	model_url = (
		"https://storage.googleapis.com/mediapipe-models/face_landmarker/"
		"face_landmarker/ float16/1/face_landmarker.task"
	).replace("/ float16", "/float16")
	print("Downloading the MediaPipe face landmark model...")
	urllib.request.urlretrieve(model_url, model_path)


def run(camera_index: int, eye_threshold: float, closure_seconds: float) -> None:
	camera = cv2.VideoCapture(camera_index)
	if not camera.isOpened():
		raise RuntimeError(f"Could not open webcam at index {camera_index}")

	model_path = Path(__file__).with_name("face_landmarker.task")
	ensure_model(model_path)
	face_landmarker = FaceLandmarker.create_from_options(FaceLandmarkerOptions(
		base_options=BaseOptions(model_asset_path=str(model_path)),
		running_mode=RunningMode.VIDEO,
		num_faces=1,
		min_face_detection_confidence=0.5,
		min_face_presence_confidence=0.5,
		min_tracking_confidence=0.5,
	))
	closure_tracker = ClosureTracker(threshold_seconds=closure_seconds)
	alert_was_active = False

	try:
		while True:
			success, frame = camera.read()
			if not success:
				print("Could not read a frame from the webcam")
				break

			frame_height, frame_width = frame.shape[:2]
			rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
			mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
			results = face_landmarker.detect_for_video(mp_image, int(time.monotonic() * 1000))
			status = "NO FACE"
			closure_duration = closure_tracker.update(False)
			alert_active = False

			if results.face_landmarks:
				face_landmarks = results.face_landmarks[0]
				left_eye = landmarks_to_points(
					face_landmarks, LEFT_EYE, frame_width, frame_height
				)
				right_eye = landmarks_to_points(
					face_landmarks, RIGHT_EYE, frame_width, frame_height
				)
				average_ratio = (
					eye_aspect_ratio(left_eye) + eye_aspect_ratio(right_eye)
				) / 2.0
				eyes_closed = average_ratio < eye_threshold
				closure_duration = closure_tracker.update(eyes_closed)
				alert_active = closure_tracker.alert_active(closure_duration)
				status = "CLOSED" if eyes_closed else "OPEN"

				for eye_points in (left_eye, right_eye):
					polygon = eye_points.astype(np.int32).reshape((-1, 1, 2))
					cv2.polylines(frame, [polygon], True, (0, 200, 255), 1)

				cv2.putText(
					frame, f"EAR: {average_ratio:.2f}", (20, 35),
					cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
				)

			if alert_active and not alert_was_active:
				play_alert()
			alert_was_active = alert_active

			status_color = (0, 0, 255) if alert_active else (0, 200, 0)
			cv2.putText(
				frame, f"EYES: {status}", (20, 70),
				cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2,
			)
			cv2.putText(
				frame,
				f"CLOSED: {closure_duration:.1f}s / {closure_seconds:.1f}s",
				(20, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.65, status_color, 2,
			)
			if alert_active:
				cv2.putText(
					frame, "DROWSINESS ALERT - TAKE A BREAK",
					(20, frame_height - 30), cv2.FONT_HERSHEY_SIMPLEX,
					0.8, (0, 0, 255), 2,
				)

			cv2.imshow("SPECTRA Drowsiness Prototype", frame)
			if cv2.waitKey(1) & 0xFF == ord("q"):
				break
	finally:
		camera.release()
		face_landmarker.close()
		cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run webcam drowsiness detection")
	parser.add_argument("--camera", type=int, default=0, help="Webcam index")
	parser.add_argument(
		"--eye-threshold", type=float, default=0.21,
		help="EAR below this value means eyes are closed",
	)
	parser.add_argument(
		"--closure-seconds", type=float, default=2.0,
		help="Continuous closure duration required for an alert",
	)
	return parser.parse_args()


if __name__ == "__main__":
	arguments = parse_args()
	run(arguments.camera, arguments.eye_threshold, arguments.closure_seconds)
