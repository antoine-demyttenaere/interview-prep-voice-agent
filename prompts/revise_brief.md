<!--
revise_brief.md - added after the documents when you choose "request changes" at the brief review.
Used in the USER message of prepare_brief (Sonnet), so Claude rewrites the brief with your changes.
Placeholders: $previous_brief (JSON), $feedback (what you typed).
-->

You already wrote this brief:

<previous_brief>
$previous_brief
</previous_brief>

The candidate reviewed it and asked for these changes:

<requested_changes>
$feedback
</requested_changes>

Write the full brief again with these changes applied. Keep everything else as it was unless the changes require otherwise.
