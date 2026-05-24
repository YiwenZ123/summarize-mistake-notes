# Question JSON Schema

Use this reference only when preparing import JSON, debugging `db validate`, or explaining why an import was rejected.

## Minimal JSON

```json
{
  "course": "Calculus",
  "collection_type": "mistake_set",
  "topic": "Derivative Applications",
  "items": [
    {
      "title": "Confusing derivative and function value",
      "original_question": "Why is f'(2) not the same as f(2)?",
      "knowledge_points": ["Geometric meaning of derivatives"],
      "mistake_reason": "Confused function value with instantaneous rate of change.",
      "correct_approach": "Distinguish the function output from the derivative value.",
      "answer_points": ["A derivative gives instantaneous rate of change."],
      "review_suggestion": "Redo three tangent-slope problems."
    }
  ]
}
```

## Fields

| Field | Requirement |
|---|---|
| `course` | Required confirmed course name. |
| `collection_type` | Required; only `mistake_set` or `question_set`. |
| `topic` | Required short topic. |
| `items` | Required non-empty list. |
| `title` | Required short title. |
| `original_question` | Required original question or prompt. |
| `knowledge_points` | Required non-empty list, usually 1-4 items. |
| `mistake_reason` | Required specific cause; for question sets use `Needs review` when no mistake exists. |
| `correct_approach` | Required correct reasoning or steps. |
| `answer_points` | Required non-empty answer-point list. |
| `review_suggestion` | Required actionable review suggestion. |

Run validation before adding uncertain JSON:

```powershell
<python> <script> db validate --input "<prepared-json-file>"
```
