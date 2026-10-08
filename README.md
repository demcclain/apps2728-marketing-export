# Apps 2728

Pulls 2027-28 applications from a Cognito Forms OData feed, cleans them up, and exports them as CSV files.

## What it does

1. **Pulls the data.** Reads the feed URL from the `ODATA` variable in `.env` and downloads every entry in pages of 500. Only the columns listed in `COLUMNS` at the top of `main.py` are kept, and they're renamed to short names (`child_name`, `guardian_name`, `phone`, `email`, `date_submitted`, `id`).

2. **Cleans phone and email.** Invalid values become blank.
   - Phone: strips everything but digits, removes a leading `1`, checks that it's a valid 10-digit US number, and formats it as `+1XXXXXXXXXX`.
   - Email: trims spaces, lowercases it, and checks that it looks like `name@domain.tld`.

3. **Cleans names** (child and guardian):
   - Removes extra spaces.
   - Removes middle initials (a single letter with a space on each side).
   - Capitalizes only the first letter of each word (`jOHN SMITH` → `John Smith`).
   - Removes repeated words (`Garza Garza` → `Garza`).
   - Splits on the first space into first and last name (`Maria De La Cruz` → `Maria` / `De La Cruz`). The four first/last columns go first and the full-name columns are dropped.

4. **Converts the submit date** from UTC to Eastern time.

5. **Exports CSVs** to `BPA DataTeam - Documents\DataFiles\Apps 2728 exports`:
   - `apps_2728_YYYY-MM-DD.csv`: one file for each submission date (Eastern).
   - `apps_2728_all.csv`: every date combined.
   - Only applications from **yesterday and earlier** are included, so a day's file is complete once it's written.
   - If a file already exists, it's deleted and rewritten, so every run refreshes all files.
   - The CSVs leave out `date_submitted` and `id`.

6. **Logs the run.** When run as a script, it adds a row to the Supabase `serverlogs` table named `apps2728 Marketing Export`, with `sucess` set to true or false. It needs `DATABASE_URL` in `.env`. A logging failure never stops the export, and runs in the notebook aren't logged.

## Running it

```bash
uv run python main.py
```

`main.py` also works as a notebook. Each `# %%` marks a cell you can run one at a time in VS Code.
