#!/usr/bin/env bash
#
# Bootstrap the local development environment for Echoes.
#
#   ./setup_env.sh              create (or update) the conda env and install the package
#   ./setup_env.sh --recreate   delete the env first, then create it from scratch
#
# Idempotent: safe to run repeatedly.

set -euo pipefail

ENV_NAME="echoes"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECREATE=0

for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=1 ;;
    -h|--help) sed -n '3,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$1"; }
fail()  { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; exit 1; }

# --- 1. conda must be available -------------------------------------------
if ! command -v conda >/dev/null 2>&1; then
  fail "conda not found on PATH. Install Miniconda or Anaconda first."
fi

# Make 'conda activate' usable from a non-interactive script.
CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1091
source "${CONDA_BASE}/etc/profile.d/conda.sh"

cd "${PROJECT_DIR}"

# --- 2. create or update the environment ----------------------------------
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  if [[ "${RECREATE}" -eq 1 ]]; then
    info "Removing existing '${ENV_NAME}' environment"
    conda env remove --name "${ENV_NAME}" --yes
    info "Creating '${ENV_NAME}' from environment.yml"
    conda env create --file environment.yml
  else
    info "Updating existing '${ENV_NAME}' environment"
    conda env update --file environment.yml --prune
  fi
else
  info "Creating '${ENV_NAME}' from environment.yml"
  conda env create --file environment.yml
fi

# --- 3. install the project in editable mode ------------------------------
info "Activating '${ENV_NAME}'"
conda activate "${ENV_NAME}"

info "Installing echoes in editable mode"
pip install --editable ".[dev]" --no-deps --quiet
pip install --quiet "responses>=0.25"

# --- 4. local configuration -----------------------------------------------
if [[ ! -f .env ]]; then
  info "Creating .env from .env.example"
  cp .env.example .env
  warn "Fill in your real values in .env before running. It is gitignored."
else
  info ".env already exists - leaving it untouched"
fi

mkdir -p state

# --- 5. verify -------------------------------------------------------------
info "Verifying the installation"
python -c "import echoes; print(f'echoes {echoes.__version__} importable')"

cat <<EOF

Setup complete.

  conda activate ${ENV_NAME}

  echoes collect            read both pools from Notion (read-only, no state)
  echoes run --dry-run      full daily run, nothing sent or written
  echoes run                the real daily run
  echoes show               what is scheduled for today
  pytest                    run the test suite

Delivery defaults to email, which requires EMAIL_FROM_ADDRESS/EMAIL_APP_PASSWORD/
EMAIL_TO_ADDRESS to be filled in. Set DELIVERY_MODE=console in .env instead
while those aren't ready yet.
EOF
