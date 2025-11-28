# Dockerfile for verilog-axi test environment
# Based on the GitHub Actions workflow configuration

FROM ubuntu:22.04

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-venv \
    python3-pip \
    iverilog \
    git \
    make \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.10 as the default python
RUN ln -sf /usr/bin/python3.10 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.10 /usr/bin/python

# Upgrade pip
RUN python -m pip install --upgrade pip

# Set working directory
WORKDIR /workspace

# Copy requirements file first for better layer caching
COPY requirements.txt /workspace/

# Install Python dependencies
RUN pip install -r requirements.txt

# Copy the project files
COPY . /workspace

# Default command runs all tests
CMD ["pytest", "-n", "auto", "--verbose", "tb"]
