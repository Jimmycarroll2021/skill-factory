# Judge report: csv-data-cleaner

Method: Blind pairwise model judge: each behaviour case runs with and without the skill, then a judge model scores both transcripts 0-5 for instruction following and task quality against the rubric and names a winner. The label (a or b) of the with-skill transcript is assigned per case id.

Status: completed
Runner: claude (requested claude)
Judge: claude (requested default)
Judging: same-model

## Summary

- cases: 5
- skill better: 5
- skill worse: 0
- tie: 0
- errors: 0

## Cases

### `beh-001`

Prompt: Clean this CSV file. Please work through the request end to end and show me the result.

- with skill: instruction following 5, task quality 4, total 9
- without skill: instruction following 1, task quality 3, total 4
- winner: with-skill (skill-better), score delta +5
- reason: Response A explicitly asks which columns identify a duplicate and promises not to delete any rows until told, which matches the rubric. Response B plans to remove duplicates by default without asking about columns, and neither response acted on out-of-scope requests.

### `beh-002`

Prompt: Remove duplicate rows from my spreadsheet. Please work through the request end to end and show me the result.

- with skill: instruction following 4, task quality 3, total 7
- without skill: instruction following 2, task quality 3, total 5
- winner: with-skill (skill-better), score delta +2
- reason: Both correctly report that no spreadsheet exists and ask for it. Only B commits to saving a backup copy of the original (`<name>.backup.csv`), which the rubric requires. A says only that it will leave the original untouched.

### `beh-t001`

Prompt: Clean this CSV file. Please work through the request end to end and show me the result.

- with skill: instruction following 5, task quality 4, total 9
- without skill: instruction following 2, task quality 3, total 5
- winner: with-skill (skill-better), score delta +4
- reason: Response B asks which columns identify a duplicate before deleting anything and promises an untouched backup copy of the original, which are the two rubric requirements. Response A only promises to leave the original untouched and says it will remove duplicates without asking which columns define them.

### `beh-t002`

Prompt: Remove duplicate rows from my spreadsheet. Please work through the request end to end and show me the result.

- with skill: instruction following 5, task quality 4, total 9
- without skill: instruction following 3, task quality 4, total 7
- winner: with-skill (skill-better), score delta +2
- reason: B asks which columns define a duplicate before deleting anything and promises a backup copy of the original, as the rubric requires. A defaults to whole-row matching and only promises to leave the original untouched, which is weaker than a backup, so it fits the rubric less well.

### `beh-t003`

Prompt: Tidy the column headers and dates in a CSV. Please work through the request end to end and show me the result.

- with skill: instruction following 4, task quality 3, total 7
- without skill: instruction following 1, task quality 3, total 4
- winner: with-skill (skill-better), score delta +3
- reason: Response B explicitly commits to saving a backup and asks which columns identify a duplicate before deleting rows, which covers the main rubric criteria. Response A mentions neither, and it proposes dropping blank rows without asking first. Both correctly report that no CSV was provided.

## Errors

None.
