"""Production voice interface for LegacyPilot, powered by Gradium STT.

The module is shared by the command-line interface and the localhost web app.
It records one microphone turn, streams 24 kHz PCM to Gradium, then returns a
plain-text request that can be submitted to ``engine.ask``.

    python -m sdk_product.voice
    python -m sdk_product.voice --dry-run
"""

import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Sequence

DEFAULT_REQUEST = (
    "Increase wing span by 12 percent. How do mass and center of gravity change?"
)
SAMPLE_RATE = 24_000
FRAME_SAMPLES = 1_920  # 80 ms, Gradium's recommended realtime frame size.


def normalize_request(request: str) -> str:
    normalized = " ".join(request.split())
    if not normalized:
        raise ValueError("the spoken request is empty")
    return normalized


def _env_value(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value:
        return value.strip()

    # Support both `sdk/.env` (normal monorepo layout) and the repository-root
    # `.env` used by existing local worktrees. Neither file is committed.
    here = Path(__file__).resolve()
    for env_file in (here.parents[1] / ".env", here.parents[2] / ".env"):
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                return candidate.strip().strip('"').strip("'")
    return None


def gradium_api_key() -> str:
    key = _env_value("GRADIUM_API_KEY")
    if not key:
        raise RuntimeError(
            "GRADIUM_API_KEY is missing; export it or add it to an ignored .env file"
        )
    return key


def _turn_has_ended(message: dict, heard_text: bool, threshold: float) -> bool:
    """Use Gradium's two-second semantic-VAD horizon when VAD is enabled."""
    if not heard_text or message.get("type") != "step":
        return False
    candidates = [
        item for item in (message.get("vad") or [])
        if float(item.get("horizon_s", 0.0)) >= 2.0
    ]
    return bool(candidates) and float(candidates[0].get("inactivity_prob", 0.0)) > threshold


async def transcribe_microphone(
    api_key: str,
    *,
    language: str = "en",
    max_seconds: Optional[float] = 10.0,
    vad_threshold: float = 1.1,
    stop_signal: Optional[threading.Event] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """Capture one local microphone turn and return its Gradium transcript."""
    try:
        import gradium
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "voice dependencies are missing; run "
            "`uv pip install -r requirements-voice.txt` with Python 3.10+"
        ) from exc

    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
    stop_capture = asyncio.Event()
    transcript: list[str] = []

    def on_audio(indata, _frames, _time, status) -> None:
        if status:
            print(f"[microphone] {status}", file=sys.stderr)
        chunk = bytes(indata)

        def enqueue() -> None:
            if not audio_queue.full():
                audio_queue.put_nowait(chunk)

        loop.call_soon_threadsafe(enqueue)

    client = gradium.client.GradiumClient(api_key=api_key)
    async with client.stt_realtime(
        model_name="default",
        input_format="pcm",
        json_config={"language": language, "delay_in_frames": 16},
        wait_for_ready_on_start=True,
    ) as stt:

        async def producer() -> None:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=on_audio,
            ):
                while not stop_capture.is_set():
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    await stt.send_audio(chunk)
            await stt.send_flush(flush_id=1)
            await stt.send_eos()

        async def consumer() -> None:
            async for message in stt:
                kind = message.get("type")
                if kind == "text" and message.get("text"):
                    transcript.append(message["text"])
                    print(message["text"], end=" ", flush=True)
                    if on_text:
                        on_text(normalize_request(" ".join(transcript)))
                elif _turn_has_ended(message, bool(transcript), vad_threshold):
                    stop_capture.set()
                elif kind == "end_of_stream":
                    return

        async def stop_monitor() -> None:
            started = loop.time()
            while not stop_capture.is_set():
                if stop_signal and stop_signal.is_set():
                    stop_capture.set()
                    return
                if max_seconds is not None and loop.time() - started >= max_seconds:
                    stop_capture.set()
                    return
                await asyncio.sleep(0.05)

        print("Speak now…", flush=True)
        try:
            await asyncio.gather(producer(), consumer(), stop_monitor())
        except sd.PortAudioError as exc:
            raise RuntimeError(
                "microphone unavailable; grant Microphone access to the terminal in "
                "System Settings > Privacy & Security, then retry"
            ) from exc

    print()
    return normalize_request(" ".join(transcript))


def launch_request(request: str, *, allow_slow: bool = False) -> int:
    from engine import ask

    report = ask(normalize_request(request), fast_only=not allow_slow)
    print(json.dumps(report, indent=2))
    return 1 if report.get("error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate LegacyPilot by voice")
    parser.add_argument("--text", help="skip STT and submit this request directly")
    parser.add_argument("--language", default="en", help="Gradium language hint")
    parser.add_argument("--max-seconds", type=float, default=10.0)
    parser.add_argument("--vad-threshold", type=float, default=1.1)
    parser.add_argument("--allow-slow", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="transcribe without running OpenVSP")
    parser.add_argument("--show-default-request", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show_default_request:
        print(DEFAULT_REQUEST)
        return 0

    request = normalize_request(args.text) if args.text else asyncio.run(
        transcribe_microphone(
            gradium_api_key(),
            language=args.language,
            max_seconds=args.max_seconds,
            vad_threshold=args.vad_threshold,
        )
    )
    print(f'Gradium transcript: "{request}"')
    return 0 if args.dry_run else launch_request(request, allow_slow=args.allow_slow)


if __name__ == "__main__":
    raise SystemExit(main())
