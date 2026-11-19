"""Evaluations: measure how well the interviewer does its job, on fixed test cases.

Each folder is one evaluation of one part of the interviewer:
- answer_scoring/: does evaluate_answer give the right score and follow-up decision?
Results go to LangSmith as experiments (compare runs side by side there) and to a local
summary in <folder>/runs/ (git-ignored). Run them with ./scripts/eval.ps1.
"""
