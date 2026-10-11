# Challenge: skill-factory c001

Verdict: **candidate-better**

## Validation

- stable: ok (0 errors, 0 warnings)
- candidate: ok (0 errors, 0 warnings)
- new candidate errors: none

## Cases

7 cases: 0 regressions, 1 improvements, 6 unchanged.

| id | kind | prompt | stable | candidate | change |
| --- | --- | --- | --- | --- | --- |
| `cap-001` | positive | Help me write a SKILL.md file so my agent learns this workflow | fail | pass | improvement |
| `stable/pos-001` | positive | Turns a plain-language description into a validated, tested and versioned agent skill for Claude Code or Codex, using the skillz CLI. | pass | pass | unchanged |
| `stable/neg-001` | negative | What is the weather forecast for tomorrow in Paris? | pass | pass | unchanged |
| `stable/neg-002` | negative | Write a haiku about autumn leaves. | pass | pass | unchanged |
| `stable/neg-003` | negative | Translate the sentence 'good morning, my friend' into Spanish. | pass | pass | unchanged |
| `stable/neg-004` | negative | Explain how photosynthesis works in plants. | pass | pass | unchanged |
| `stable/neg-005` | negative | Suggest a recipe for a vegetarian lasagne. | pass | pass | unchanged |

## Weak areas

- stable: trigger-vocabulary, no-behaviours
- candidate: no-behaviours
- resolved: trigger-vocabulary
- introduced: none

## Notes

Grading is the offline lexical-overlap heuristic (threshold 0.3); no model was consulted.
Promotion is a separate human command: `skillz promote skill-factory c001`.
