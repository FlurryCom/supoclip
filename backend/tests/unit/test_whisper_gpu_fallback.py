from pathlib import Path

import pytest
import torch

from src.media import transcription


class FakeModel:
    def __init__(self, device, fail=False):
        self.device, self.fail, self.calls = device, fail, []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 20.00 MiB.")
        return {"segments": [], "text": "ok", "device": self.device}


@pytest.fixture
def models(monkeypatch):
    loaded = {}

    def load_model(name, device=None):
        loaded[device] = FakeModel(device or "cuda", fail=device is None)
        return loaded[device]

    monkeypatch.setattr(transcription, "_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(transcription, "_WHISPER_MODEL_CACHE", {})
    monkeypatch.setattr(transcription._whisper, "load_model", load_model)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    return loaded


def test_gpu_out_of_memory_finishes_on_the_cpu(models):
    result = transcription.transcribe_with_whisper(Path("/tmp/a.mp4"), "small", "pt")

    assert result["device"] == "cpu"
    assert models["cpu"].calls == [{"word_timestamps": True, "language": "pt", "fp16": False}]
    # Both stay cached: the GPU copy for later files, the CPU one for the next shortage.
    assert set(transcription._WHISPER_MODEL_CACHE) == {"small", "small@cpu"}


def test_other_errors_are_not_swallowed(monkeypatch, models):
    def broken(path, **kwargs):
        raise RuntimeError("bad audio")

    monkeypatch.setattr(transcription, "_get_whisper_model", lambda name, device=None: type("M", (), {"transcribe": staticmethod(broken)})())
    with pytest.raises(RuntimeError, match="bad audio"):
        transcription.transcribe_with_whisper(Path("/tmp/a.mp4"), "small")
