<!--
ask.md - the conversation so far plus what the interviewer should say next.
Used as the USER prompt by the ask node (Haiku); the system prompt is interviewer_system.md.
Claude replies with plain text: exactly what the interviewer says (it is spoken aloud).
Placeholders: $transcript, $context (passages of the candidate's documents found by search for
this question, app/rag/retrieve.py), $directive (filled from ask_question.md or ask_followup.md).
-->

The interview so far:

<transcript>
$transcript
</transcript>

Passages from the candidate's documents that match the next question (from their CV, the job post or emails):

<context>
$context
</context>

$directive

If a passage gives a specific detail that would make the question sharper (a project, a number, a requirement of the role), you may use it, the way an interviewer who read the CV would. Do not read passages out or mention that you searched documents.

Reply with only what you say out loud, nothing else.
