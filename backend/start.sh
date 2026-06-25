#!/bin/bash
# Start iQmaxer server with API keys from Hermes env
cd /var/www/iqmaxer/backend || exit 1

# Source Hermes env (sets vars in bash), then export needed ones
source /root/.hermes/.env 2>/dev/null

# .env sets vars without "export" keyword, so child processes don't see them
# Explicitly export the ones our backend needs
export OPENCODE_GO_API_KEY
export OPENCODE_GO_BASE_URL

rm -f iqmaxer.db*
python3 main.py > /tmp/iqmaxer-server.log 2>&1
