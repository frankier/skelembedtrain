#!/usr/bin/env bash

poetry install
git submodule update --init --recursive
FORCE_CUDA=1 \
    TORCH_CUDA_ARCH_LIST='3.5;3.7;5.0;5.2;5.3;6.0;6.1;6.2;7.0;7.2;7.5;3.5+PTX;3.7+PTX;5.0+PTX;5.2+PTX;5.3+PTX;6.0+PTX;6.1+PTX;6.2+PTX;7.0+PTX;7.2+PTX;7.5+PTX' \
    poetry run pip install -e $(pwd)/submodules/mmskeleton
