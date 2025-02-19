# Build stage
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    python3-dev=3.11.* \
    gcc=4:12.2.* \
    libffi-dev=3.4.* \
    libc-ares-dev=1.18.* \
    && rm -rf /var/lib/apt/lists/*

# Create venv and install requirements
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

# Add labels
LABEL maintainer="martiantux@proton.me"
LABEL org.opencontainers.image.source="https://github.com/martiantux/redditarr"
LABEL org.opencontainers.image.description="Reddit Content Archival Tool"
LABEL org.opencontainers.image.version="0.5.0"

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl=7.88.* \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -r -u 1000 -m app

# Copy Python virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Create app directories
RUN mkdir -p /app /config /media /metadata && \
    chown -R app:app /app /config /media /metadata

# Set working directory and switch to non-root user
WORKDIR /app
USER app

# Copy application code
COPY --chown=app:app . .

# Expose port
EXPOSE 80

# Volume configuration
VOLUME ["/config", "/media", "/metadata"]

# Health check
HEALTHCHECK --interval=60s --timeout=30s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:80/api/health || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]