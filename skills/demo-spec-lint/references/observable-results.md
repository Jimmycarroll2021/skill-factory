# Observable results: what makes a golden-path step testable

Confidence key: 🟢 read in the primary source · 🟡 secondary source or widely
used practice · 🔴 this repo's convention.

## The rule

A step is testable when its result names something a script can see after the
action: text on screen, an element's state, a count, a URL, a response code, a
file. If two honest people could disagree about whether the result happened, it
is not observable yet. 🔴

The shape is action → visible result, one result per step:

    2. Click "Mark blocker" on Tom's card → Tom's card moves to the top with a red "1 blocker" badge

This is the "Then" of Given-When-Then: a scenario's outcome is stated as an
observable consequence of the action, so it can be checked. 🟢 [1] [2]

## Rewrites

| Untestable | Testable |
|---|---|
| Open the dashboard | Open / → the dashboard lists 5 items, each with a quantity |
| Save the form → it works | Save the form → the list shows the new value and a "saved" message |
| Import the CSV → data is correct | Import week.csv → 7 rows appear and the total reads 1,240 |
| User can see their history | Open /history → the last 3 orders appear newest first |

Prefer results that come from the seed data, because the person writing the
smoke test can then assert exact values. 🔴

## Why the linter blocks rather than warns

A step with no result does not fail any test; it is simply never asserted, so
the demo can pass with that step broken. That failure is silent, so it has to
be stopped before building starts. 🔴 Vague single-word results ("works") are only warnings,
because a human can still judge them; the linter cannot tell a good result from
a wrong one. 🔴

## Sources

1. Martin Fowler, "GivenWhenThen" (bliki), https://martinfowler.com/bliki/GivenWhenThen.html
2. Cucumber, "Gherkin Reference" (Given / When / Then steps), https://cucumber.io/docs/gherkin/reference/
