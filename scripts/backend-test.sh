#!/bin/sh
# Run backend unit tests in a throwaway container from the built backend image.
cd ~/projects/supoclip && docker run --rm -v ./backend/tests:/app/tests:z,ro -v ./backend/src:/app/src:z,ro -v ./backend/fonts:/app/fonts:z,ro -v ./backend/assets:/app/assets:z,ro --entrypoint sh supoclip-backend -c "cd /app && (uv pip install -q --python /app/.venv/bin/python pytest pytest-asyncio >/dev/null 2>&1 || /app/.venv/bin/python -m ensurepip -q); /app/.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov $*"
