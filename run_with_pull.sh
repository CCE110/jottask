#!/bin/bash
cd ~/Developer/jottask
git pull origin main 2>&1
python3 "$@"
