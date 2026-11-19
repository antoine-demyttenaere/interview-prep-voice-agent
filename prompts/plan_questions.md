<!--
plan_questions.md - asks for the list of main questions for this session.
Used as the USER prompt by plan_questions (Sonnet); the system prompt is interviewer_system.md,
so Claude already has the brief. Answer shape: QuestionPlan (app/graph/schemas.py).
Placeholders: $num_questions (NUM_QUESTIONS setting, 5 by default), $rubric (from rubrics/),
$bank_questions (closest seeded questions from question_bank/, for the technical modes only).
-->

Before the interview starts, plan exactly $num_questions main questions for this session.

- Draw on the brief: the role requirements, the focus areas, the probable questions and the gaps in the candidate's CV. Cover different areas; no two questions should test the same thing.
- Order them as a real interviewer would: start with an easier opener, put the hardest questions in the middle.
- Write each question as you would actually say it out loud.
- Answers will be scored with this rubric, so each question should give the candidate room to show these criteria:

$rubric

Seeded questions that match this interview (use, adapt or ignore them; tailor them to the company and the role):

<question_bank>
$bank_questions
</question_bank>

For each question, say in based_on what it comes from: the brief item, the document, or "question bank: <topic>".
