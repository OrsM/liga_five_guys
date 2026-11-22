#!/usr/bin/env bash
# Every check of the repo: the gate, in full (tools/gate has the suite list and the contract).
exec "$(dirname "${BASH_SOURCE[0]}")/gate" --full "$@"
