# Idea

A command-line tool for small cafe owners that reads the daily sales CSV their point-of-sale system exports, works out which menu items sold out and at what time, and suggests tomorrow's prep quantities so they stop running out of the bestsellers by noon and stop binning unsold pastries at close. Today they eyeball last week's numbers on paper.

## Constraints

- Stack: Python 3.12, managed with uv
- Must run offline on a laptop; no accounts, no cloud services
- Input: one CSV per day in a folder; output: a printed prep list plus a CSV
