# Build stage
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    python3-dev \
    gcc \
    libffi-dev \
    libc-ares-dev \
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
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Create app directories
RUN mkdir -p /app /app/metadata /app/media /app/static

# Set working directory
WORKDIR /app

# Copy application code
COPY . .

# Create entrypoint script for permission handling
RUN echo '#!/bin/bash\n\
# Apply ownership if PUID/PGID provided\n\
if [ -n "${PUID}" ] && [ -n "${PGID}" ]; then\n\
  echo "Setting permissions with PUID: ${PUID}, PGID: ${PGID}"\n\
  chown -R ${PUID}:${PGID} /app/metadata /app/media\n\
fi\n\
# Run the application\n\
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 80\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

# Expose port
EXPOSE 80

# Define volumes
VOLUME ["/app/metadata", "/app/media"]

# Health check
HEALTHCHECK --interval=60s --timeout=30s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:80/api/health || exit 1

# Run application
ENTRYPOINT ["/entrypoint.sh"]