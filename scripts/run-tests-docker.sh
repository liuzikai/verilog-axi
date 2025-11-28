#!/bin/bash
# Script to run tests using Docker
#
# Usage:
#   ./scripts/run-tests-docker.sh           # Run all tests
#   ./scripts/run-tests-docker.sh --build   # Rebuild the Docker image before running tests
#   ./scripts/run-tests-docker.sh --shell   # Start an interactive shell in the container
#   ./scripts/run-tests-docker.sh <args>    # Pass custom arguments to pytest
#
# Examples:
#   ./scripts/run-tests-docker.sh -k test_axi_ram   # Run only axi_ram tests
#   ./scripts/run-tests-docker.sh --verbose -x      # Verbose output, stop on first failure

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_NAME="verilog-axi-test"

# Parse arguments
BUILD_IMAGE=false
INTERACTIVE=false
PYTEST_ARGS=""

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_IMAGE=true
            ;;
        --shell)
            INTERACTIVE=true
            ;;
        *)
            PYTEST_ARGS="${PYTEST_ARGS} ${arg}"
            ;;
    esac
done

# Build the Docker image if requested or if it doesn't exist
if [ "$BUILD_IMAGE" = true ] || ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
    echo "Building Docker image..."
    docker build -t "$IMAGE_NAME" "$PROJECT_ROOT"
fi

# Run the container
if [ "$INTERACTIVE" = true ]; then
    echo "Starting interactive shell..."
    docker run --rm -it \
        -v "${PROJECT_ROOT}:/workspace" \
        "$IMAGE_NAME" \
        /bin/bash
elif [ -n "$PYTEST_ARGS" ]; then
    echo "Running tests with custom arguments:$PYTEST_ARGS"
    docker run --rm \
        -v "${PROJECT_ROOT}:/workspace" \
        "$IMAGE_NAME" \
        pytest -n auto --verbose tb $PYTEST_ARGS
else
    echo "Running all tests..."
    docker run --rm \
        -v "${PROJECT_ROOT}:/workspace" \
        "$IMAGE_NAME"
fi
