"""
实时语音识别 — 麦克风捕获 + 豆包 ASR（优先）+ 腾讯云 ASR（备用）。

Ctrl+Y 开始/停止监听，识别的句子累积显示。
"""
import threading
import time
import io
from typing import List, Optional
from PyQt6.QtCore import QObject, pyqtSignal


class SpeechSignal(QObject):
    sentence_ready = pyqtSignal(str, list)
    error_occurred = pyqtSignal(str)


class SpeechWorker:
    """后台实时语音识别线程。"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sentences: List[str] = []
        self._signal = SpeechSignal()
        self._on_error = None

    @property
    def signal(self) -> SpeechSignal:
        return self._signal

    @property
    def sentences(self) -> List[str]:
        return list(self._sentences)

    def clear(self):
        self._sentences = []

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def _asr_recognize(self, wav_bytes: bytes) -> Optional[str]:
        """腾讯云一句话识别。"""
        import base64
        from utils.api_key_manager import get_tencent_secret_id, get_tencent_secret_key
        sid = get_tencent_secret_id()
        skey = get_tencent_secret_key()
        if not sid or not skey:
            return None
        try:
            from tencentcloud.common import credential
            from tencentcloud.asr.v20190614 import asr_client, models
            b64 = base64.b64encode(wav_bytes).decode("utf-8")
            cred = credential.Credential(sid, skey)
            client = asr_client.AsrClient(cred, "ap-guangzhou")
            req = models.SentenceRecognitionRequest()
            req.EngSerViceType = "16k_zh"
            req.SourceType = 1
            req.VoiceFormat = "wav"
            req.Data = b64
            req.DataLen = len(wav_bytes)
            resp = client.SentenceRecognition(req)
            text = resp.Result if resp.Result else ""
            if text:
                print(f"[ASR] {text}")
            return text.strip() if text else None
        except Exception as e:
            if "RequestLimitExceeded" not in str(e):
                print(f"[ASR] {str(e)[:80]}")
            return None

    def _listen_loop(self):
        try:
            import pyaudio
        except ImportError:
            self._signal.error_occurred.emit("请安装: pip install pyaudio")
            return

        RATE = 16000
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        SILENCE_LIMIT = int(0.8 / (CHUNK / RATE))  # 0.8 秒静音即断句
        MAX_RECORD = int(8.0 / (CHUNK / RATE))

        pa = pyaudio.PyAudio()
        dev_idx = None
        for i in range(pa.get_device_count()):
            if pa.get_device_info_by_index(i)["maxInputChannels"] > 0:
                dev_idx = i
                break

        if dev_idx is None:
            self._signal.error_occurred.emit("未找到麦克风")
            pa.terminate()
            return

        stream = None
        try:
            stream = pa.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                             input=True, input_device_index=dev_idx,
                             frames_per_buffer=CHUNK)
        except Exception as e:
            self._signal.error_occurred.emit(f"麦克风: {e}")
            pa.terminate()
            return

        while self._running:
            frames = []
            silence_count = 0
            started = False

            while self._running and not started:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    break
                vol = max(abs(int.from_bytes(data[i:i+2], 'little', signed=True))
                          for i in range(0, len(data), 2)) if len(data) >= 2 else 0
                if vol > 150:
                    started = True
                    frames.append(data)
                    silence_count = 0
                else:
                    time.sleep(0.01)

            if not started:
                continue

            while self._running and silence_count < SILENCE_LIMIT and len(frames) < MAX_RECORD:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    break
                frames.append(data)
                vol = max(abs(int.from_bytes(data[i:i+2], 'little', signed=True))
                          for i in range(0, len(data), 2)) if len(data) >= 2 else 0
                if vol < 150:
                    silence_count += 1
                else:
                    silence_count = 0

            wav_buf = io.BytesIO()
            import wave
            with wave.open(wav_buf, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(pa.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))

            text = self._asr_recognize(wav_buf.getvalue())
            if text:
                self._sentences.append(text)
                self._signal.sentence_ready.emit(text, list(self._sentences))
            else:
                self._signal.sentence_ready.emit("", list(self._sentences))

        stream.stop_stream()
        stream.close()
        pa.terminate()


_worker: Optional[SpeechWorker] = None


def get_speech_worker() -> SpeechWorker:
    global _worker
    if _worker is None:
        _worker = SpeechWorker()
    return _worker
