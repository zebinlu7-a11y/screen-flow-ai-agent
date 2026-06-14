"""
实时语音识别 — 麦克风捕获 + Google STT 转文字。

Ctrl+Y 开始/停止监听，识别的句子累积显示。
"""
import threading
import time
from typing import List, Optional


class SpeechWorker:
    """后台实时语音识别线程。"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sentences: List[str] = []         # 所有识别到的句子
        self._on_sentence = None                 # 回调: (sentence, all_sentences)
        self._on_error = None

    def set_callbacks(self, on_sentence=None, on_error=None):
        self._on_sentence = on_sentence
        self._on_error = on_error

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

    def _listen_loop(self):
        """后台循环：持续监听 + 识别。"""
        try:
            import speech_recognition as sr
        except ImportError:
            if self._on_error:
                self._on_error("请安装: pip install SpeechRecognition pyaudio")
            return

        r = sr.Recognizer()
        r.energy_threshold = 1000        # 环境噪声阈值
        r.dynamic_energy_threshold = True
        r.pause_threshold = 0.8          # 说话停顿阈值（秒）

        try:
            mic = sr.Microphone()
            with mic as source:
                r.adjust_for_ambient_noise(source, duration=1)
        except Exception as e:
            if self._on_error:
                self._on_error(f"麦克风不可用: {e}")
            return

        while self._running:
            try:
                with mic as source:
                    # 监听一段语音（最多5秒静音后停止）
                    audio = r.listen(source, timeout=5, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                continue
            except Exception:
                time.sleep(0.1)
                continue

            # 后台线程中识别
            try:
                text = r.recognize_google(audio, language="zh-CN")
                if text and text.strip():
                    self._sentences.append(text.strip())
                    if self._on_sentence:
                        self._on_sentence(text.strip(), self._sentences)
            except sr.UnknownValueError:
                pass  # 没识别到内容
            except sr.RequestError as e:
                if self._on_error:
                    self._on_error(f"识别服务不可用: {e}")
            except Exception:
                pass


# 全局单例
_worker: Optional[SpeechWorker] = None


def get_speech_worker() -> SpeechWorker:
    global _worker
    if _worker is None:
        _worker = SpeechWorker()
    return _worker
