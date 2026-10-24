# Team standup board

pitch: a five-person team posts a daily update and sees everyone's latest side by side
run: ./demo.sh
health: /api/health
stack: Vite front end with a JSON file store

## seed
- Priya, done: shipped the export button; next: CSV import; blocked: no
- Tom, done: fixed login timeout; next: password reset; blocked: waiting on design
- Ana, done: wrote onboarding copy; next: empty states; blocked: no
- Kwame, done: profiled the board query; next: add an index; blocked: no
- Lena, done: triaged six bugs; next: release notes; blocked: no

## golden path
1. Open / → five cards show each person's latest update, newest first
2. Click "Mark blocker" on Tom's card → Tom's card moves to the top with a red "1 blocker" badge
3. Post an update as Ana with "done: empty states" → Ana's card shows the new text and today's date

## out of scope
- authentication
- notifications
