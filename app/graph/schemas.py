"""Shapes of the structured answers Claude must return, written as Pydantic models.

app/llm.structured() sends one of these classes to Claude as a JSON schema. Claude's reply is
guaranteed to match it and comes back as a validated Python object. Field descriptions are part
of the schema, so they also work as instructions to Claude.

- InterviewBrief: written by prepare_brief.     - QuestionPlan: by plan_questions.
- AnswerEvaluation: by evaluate_answer.         - FinalFeedback: by final_feedback.

Number ranges (scores 1-5) are written in descriptions and prompts rather than as validators,
so the schemas stay compatible with structured outputs.
"""

from pydantic import BaseModel, Field


class Interviewer(BaseModel):
    name: str
    role: str = Field(description="Their job title or role in the process, if known")
    notes: str = Field(description="One short phrase from the documents; empty if nothing")


class InterviewBrief(BaseModel):
    """Read in under two minutes before practising: every item is short. Limits are in the descriptions."""

    company_summary: str = Field(
        description="2 short sentences: what the company does, and the one fact that matters most for this interview"
    )
    role_requirements: list[str] = Field(
        description="The 4 or 5 most important requirements, most important first, each under 10 words"
    )
    interview_format: str = Field(description="1 or 2 short sentences: who, how long, what kind of questions")
    interviewers: list[Interviewer]
    cv_gaps: list[str] = Field(description="The 2 or 3 biggest gaps between the CV and the role, one short sentence each")
    focus_areas: list[str] = Field(
        description="The 3 things to prepare most, one short sentence each, starting with a verb"
    )
    probable_questions: list[str] = Field(description="The 5 most likely questions, one sentence each")


class PlannedQuestion(BaseModel):
    text: str = Field(description="The question as the interviewer would ask it")
    topic: str = Field(description="Short topic label, e.g. 'conflict', 'ownership', 'role fit'")
    rationale: str = Field(description="Why this question, linked to the brief")
    based_on: str = Field(
        description="Where it comes from: the brief item, document or question-bank entry it is based on"
    )


class QuestionPlan(BaseModel):
    questions: list[PlannedQuestion]


class CriterionScore(BaseModel):
    criterion: str = Field(description="The rubric criterion id")
    score: int = Field(description="1 to 5, per the rubric scale")
    note: str = Field(description="One sentence of evidence from the answer")


class AnswerEvaluation(BaseModel):
    scores: list[CriterionScore]
    overall: int = Field(description="1 to 5, overall quality of the answer")
    summary: str = Field(description="One or two sentences: what worked, what was missing")
    follow_up: bool = Field(description="True if a real interviewer would probe further")
    follow_up_focus: str = Field(description="What the follow-up should probe; empty if none")


class QuestionFeedback(BaseModel):
    number: int = Field(description="Number of the main question in the interview, starting at 1")
    question: str = Field(description="The main question, shortened to one line")
    score: int = Field(description="1 to 5")
    comment: str = Field(description="1 or 2 short sentences, quoting or paraphrasing the answer")
    better: str = Field(description="1 sentence: what a stronger answer would have said or shown")


class FinalCriterionScore(BaseModel):
    """One rubric criterion in the final report: the score, why, and how to raise it."""

    criterion: str = Field(description="The rubric criterion id")
    score: int = Field(description="1 to 5, per the rubric scale")
    note: str = Field(description="One short sentence of evidence from the interview")
    improve: list[str] = Field(
        description="2 concrete ideas to raise this score next time, one short sentence each, "
        "specific to this candidate's answers"
    )


class FinalFeedback(BaseModel):
    """Read right after the interview: short items, the most useful first."""

    overall_score: int = Field(description="1 to 5")
    summary: str = Field(description="2 or 3 short sentences on the whole interview")
    rubric_scores: list[FinalCriterionScore]
    per_question: list[QuestionFeedback]
    strengths: list[str] = Field(description="Up to 3, one short sentence each")
    fixes: list[str] = Field(
        description="The 3 most impactful changes for next time, one short sentence each, starting with a verb"
    )
