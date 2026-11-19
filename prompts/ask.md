<!--
ask.md - the conversation so far plus what the interviewer should say next.
Used as the USER prompt by the ask node (Haiku); the system prompt is interviewer_system.md.
Claude replies with plain text: exactly what the interviewer says (spoken aloud in Phase 2).
Placeholders: $transcript, $directive (filled from ask_question.md or ask_followup.md).
-->

The interview so far:

<transcript>
$transcript
</transcript>

$directive

Reply with only what you say out loud, nothing else.
