# Models

## nudenet-320n.onnx

Nudity detector used by the sensitive blur (`src/media/nudity_check.py`).

- Source: the `nudenet` 3.4.2 wheel on PyPI (`nudenet/320n.onnx`), project
  https://github.com/notAI-tech/nudenet
- sha256: `c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f`
- License: the package metadata says MIT; the LICENSE file shipped in the same
  wheel is the GNU AGPL-3.0. Treat it as AGPL-3.0 unless upstream clarifies.
- Only the model is used; its pre/post-processing is reimplemented here so the
  `nudenet` package (which pulls `opencv-python-headless`, clashing with
  `opencv-python`) isn't installed.
