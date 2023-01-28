# Dockerfile for BigChem Worker. Contains BigChem code and CPU-only QC Packages
# Follows https://stackoverflow.com/a/54763270/5728276
FROM mambaorg/micromamba:1.3-jammy

LABEL maintainer="Colton Hicks <colton@coltonhicks.com>"

# https://github.com/awslabs/amazon-sagemaker-examples/issues/319
ENV PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_VERSION=1.3.1 \
    # Install to system python, no need for venv
    POETRY_VIRTUALENVS_CREATE=false

# Perform root tasks
WORKDIR /code/
USER root
RUN apt-get update && \
    # for psutil in qcengine
    # https://github.com/giampaolo/psutil/blob/master/INSTALL.rst
    apt-get install -y gcc python3-dev && \
    # So $MAMBA_USER can read/write to /code/
    chown -R $MAMBA_USER /code/
USER $MAMBA_USER

# Install BigChem and QC Program
COPY --chown=$MAMBA_USER:$MAMBA_USER pyproject.toml poetry.lock docker/env.lock ./
RUN micromamba install -y -n base -f env.lock && \
    micromamba clean --all --yes
ARG MAMBA_DOCKERFILE_ACTIVATE=1  # (otherwise python will not be found)
RUN python -m pip install --upgrade pip && \ 
    python -m pip install "poetry==$POETRY_VERSION" && \
    poetry install --only main --no-interaction --no-ansi

# Copy in code
COPY --chown=$MAMBA_USER:$MAMBA_USER bigchem/ bigchem/

# Run without heartbeat, mingle, gossip to reduce network overhead
# https://stackoverflow.com/questions/66961952/how-can-i-scale-down-celery-worker-network-overhead
CMD ["sh", "-c", "celery -A bigchem.tasks worker --without-heartbeat --without-mingle --without-gossip --loglevel=INFO"]
