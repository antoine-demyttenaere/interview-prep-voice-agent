<!--
evaluate_answer.md - score the latest answer with the rubric and decide whether to follow up.
Used as the USER prompt by evaluate_answer (Haiku); the system prompt is interviewer_system.md.
Answer shape: AnswerEvaluation (app/graph/schemas.py). The code enforces the follow-up cap
(MAX_FOLLOWUPS) even if Claude asks for more.
Placeholders: $rubric, $planned_question, $exchange (this question's turns), $followups_used, $max_followups.
-->

Step out of the conversation for a moment and assess the candidate's latest answer as a fair, experienced interviewer.

The planned question was: "$planned_question"

The exchange on this question so far (the last candidate turn is the answer to assess):

<exchange>
$exchange
</exchange>

Score the answer on each criterion of this rubric, judging the whole exchange on this question, so a follow-up answer can make up for what was missing earlier:

$rubric

Then decide whether a real interviewer would ask a follow-up. Follow up when the answer is vague, misses the candidate's own contribution or the result, or makes a claim worth testing. Do not follow up on a strong, complete answer, or when the candidate clearly has nothing more to add. $followups_used of $max_followups follow-ups have been used on this question; if none are left, set follow_up to false.
