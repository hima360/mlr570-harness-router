A tiny project with one bug, to try the harness on:

    ./harness try

or the long way (works on a copy; asks before changing these files):

    ./harness run "average() crashes on an empty list; make it return 0.0 as documented" examples/average-bug --check "python -m unittest -q" --image python:3.12-slim
