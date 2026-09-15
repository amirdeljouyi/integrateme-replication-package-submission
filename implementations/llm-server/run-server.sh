#!/bin/bash

source .env/bin/activate

logDir="log/"
attempt=$1

mkdir -p $logDir
python -m run_server --model gpt-5 --port 800$1 --write_responses True --write_failed_responses True --log_level DEBUG --log_to_file True --attempt $attempt --signature adopted
#python -m main --model gpt-4o-mini --port 8000 --write_responses True --write_failed_responses True > $logDir/$(date '+%Y-%m-%d-%H-%M').log