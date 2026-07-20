# ─── HydroAtlas Backend ───────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Install OS-level dependencies required by some Python packages ────
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev libexpat1 && \
    rm -rf /var/lib/apt/lists/*

# ── Install Python dependencies ──────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy application source ─────────────────────────────────────────
COPY . .

# ── Expose the API port ─────────────────────────────────────────────
EXPOSE 8000

# ── Start uvicorn ────────────────────────────────────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]