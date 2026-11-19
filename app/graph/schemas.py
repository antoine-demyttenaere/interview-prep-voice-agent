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
    notes: str = Field(description="What the documents say about them; empty if nothing")


class InterviewBrief(BaseModel):
    company_summary: str = Field(description="What the company does, its context, 3-5 sentences")
    role_requirements: list[str] = Field(description="What the role needs, most important first")
    interview_format: str = Field(description="Likely format, stages and length of this interview")
    interviewers: list[Interviewer]
    cv_gaps: list[str] = Field(description="Gaps or weak spots between the CV and the role")
    focus_areas: list[str] = Field(description="Areas the candidate should prepare most")
    probable_questions: list[str] = Field(description="Questions this interviewer is likely to ask")


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
    question: str
    score: int = Field(description="1 to 5")
    comment: str = Field(description="Specific, quoting or paraphrasing the answer")


class FinalFeedback(BaseModel):
    overall_score: int = Field(description="1 to 5")
    summary: str = Field(description="Three or four sentences on the whole interview")
    rubric_scores: list[CriterionScore]
    per_question: list[QuestionFeedback]
    strengths: list[str]
    fixes: list[str] = Field(description="Concrete changes for next time, most impactful first")
