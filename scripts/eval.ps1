# eval.ps1 - runs the evaluation of answer scoring + follow-ups, and compares it with the last run.
#
# It runs evals/answer_scoring/run.py inside the api container: the real evaluate_answer node on
# the 36 test cases (evals/answer_scoring/cases.yaml + your private ones), graded by code. The
# results go to LangSmith as an experiment (compare runs side by side there) and to a local
# summary in evals/answer_scoring/runs/, compared with the previous run of the same kind.
# Run it after every change to a prompt, a rubric or a model, before keeping the change.
#
# Usage:  ./scripts/eval.ps1                      # all cases, 2 repetitions (~2 min, ~$0.04)
#         ./scripts/eval.ps1 --reps 1             # quicker, less precise
#         ./scripts/eval.ps1 --ids strong-incident,no-answer-skip   # a few cases only (a "pilot")
#         ./scripts/eval.ps1 --no-private         # leave out the cases from your real answers

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$extra = $args
if (-not ($extra -contains "--reps")) { $extra = @("--reps", "2") + $extra }

docker compose exec api python -m evals.answer_scoring.run @extra
exit $LASTEXITCODE
