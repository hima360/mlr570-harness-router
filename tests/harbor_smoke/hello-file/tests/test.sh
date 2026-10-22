#!/bin/bash
mkdir -p /logs/verifier
if grep -q hello /app/hello.txt 2>/dev/null; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
