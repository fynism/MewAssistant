FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /bin/

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/model-cache \
    EMBEDDING_DEVICE=cpu

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
# Load every OCR model during image construction, before processing private documents.
RUN python -c "from rapidocr import RapidOCR; from PIL import Image, ImageDraw, ImageFont; import numpy as np; image=Image.new('RGB',(600,120),'white'); ImageDraw.Draw(image).text((10,20),'SuperMew 2025 v2.0',font=ImageFont.load_default(size=40),fill='black'); RapidOCR()(np.asarray(image))"

COPY backend ./backend
COPY frontend ./frontend
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
