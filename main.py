# %%
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import requests
from dotenv import load_dotenv

load_dotenv()

ODATA_URL = os.environ["ODATA"]
CHUNK_SIZE = 500

# Columns to pull, as "original name": "new name".
COLUMNS: dict[str, str] = {
    "ApplyingChildsNameNombreDelNiñoaSolicitante": "child_name",
    "GuardianNameNombreDelTutor2": "guardian_name",
    "PhoneTeléfono": "phone",
    "EmailCorreoElectrónico2": "email",
    "Entry_DateSubmitted": "date_submitted",
    "Id": "id",
}

# %%
def fetch_chunks(url: str, columns: list[str], chunk_size: int = CHUNK_SIZE):
    """Yield one polars DataFrame per OData page, keeping only `columns`.

    Pages with $top/$skip and follows @odata.nextLink when the server provides one.
    Cognito returned 400 for a $select request, so each page is trimmed right after it arrives.
    """
    session = requests.Session()
    session.headers["Accept"] = "application/json"

    skip = 0
    next_url = None
    while True:
        if next_url:
            resp = session.get(next_url, timeout=60)
        else:
            params = {"$top": chunk_size, "$skip": skip}
            resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()

        rows = payload.get("value", [])
        if not rows:
            break
        yield pl.from_dicts(rows, infer_schema_length=None).select(columns)

        next_url = payload.get("@odata.nextLink")
        if not next_url:
            if len(rows) < chunk_size:
                break
            skip += len(rows)


# %%
chunks = []
for i, chunk in enumerate(fetch_chunks(ODATA_URL, list(COLUMNS))):
    print(f"chunk {i}: {chunk.height} rows")
    chunks.append(chunk)

# %%
df = pl.concat(chunks, how="diagonal_relaxed").rename(COLUMNS) if chunks else pl.DataFrame()
print(df.shape)

# %% Normalize phone and email (invalid values become null)
EMAIL_PATTERN = r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$"

phone_digits = pl.col("phone").cast(pl.String).str.replace_all(r"\D", "")
# Drop a leading US country code so "1XXXXXXXXXX" becomes 10 digits
phone_digits = (
    pl.when(phone_digits.str.len_chars() == 11, phone_digits.str.starts_with("1"))
    .then(phone_digits.str.slice(1))
    .otherwise(phone_digits)
)

email_clean = pl.col("email").cast(pl.String).str.strip_chars().str.to_lowercase()

df = df.with_columns(
    # Valid US number: 10 digits, area code and exchange can't start with 0 or 1
    pl.when(phone_digits.str.contains(r"^[2-9]\d{2}[2-9]\d{6}$"))
    .then(pl.lit("+1") + phone_digits)
    .otherwise(None)
    .alias("phone"),
    pl.when(email_clean.str.contains(EMAIL_PATTERN))
    .then(email_clean)
    .otherwise(None)
    .alias("email"),
)

print("invalid/missing phones:", df["phone"].null_count())
print("invalid/missing emails:", df["email"].null_count())

# %% Normalize names: drop middle initials, capitalize only the first letter of each word
NAME_COLUMNS = ["child_name", "guardian_name"]


def normalize_name(col: str) -> pl.Expr:
    name = pl.col(col).cast(pl.String).str.strip_chars().str.replace_all(r"\s+", " ")
    # A lone letter between two spaces is a middle initial. Run twice so back-to-back
    # initials ("Ana B C Diaz") are both caught, since each match consumes its spaces.
    for _ in range(2):
        name = name.str.replace_all(r" [A-Za-z] ", " ")
    return (
        name.str.split(" ")
        .list.eval(
            pl.element().str.slice(0, 1).str.to_uppercase()
            + pl.element().str.slice(1).str.to_lowercase()
        )
        # Drop repeated words within the same name ("Garza Garza" -> "Garza")
        .list.unique(maintain_order=True)
        .list.join(" ")
        .alias(col)
    )


df = df.with_columns(normalize_name(c) for c in NAME_COLUMNS)

# %% Split names into first / last on the first space ("Maria De La Cruz" -> "Maria", "De La Cruz")
for prefix in ["child", "guardian"]:
    parts = pl.col(f"{prefix}_name").str.splitn(" ", 2)
    df = df.with_columns(
        parts.struct.field("field_0").alias(f"{prefix}_first_name"),
        parts.struct.field("field_1").alias(f"{prefix}_last_name"),
    )

# Put the split name columns first and drop the full-name columns
name_parts = ["child_first_name", "child_last_name", "guardian_first_name", "guardian_last_name"]
df = df.drop(NAME_COLUMNS).select(*name_parts, pl.exclude(name_parts))

# %% Parse date submitted ("2026-10-03T17:42:59.429Z", UTC) into a real datetime in Eastern time
df = df.with_columns(
    pl.col("date_submitted")
    .str.to_datetime("%Y-%m-%dT%H:%M:%S%.fZ", time_zone="UTC")
    .dt.convert_time_zone("America/New_York")
    .dt.truncate("1s")
)

# %% Export one CSV per submission date (Eastern), only for yesterday and earlier
EXPORT_DIR = Path(
    r"C:\Users\DannyMcClain\BridgePrep Academy Charter School\BPA DataTeam - Documents"
    r"\DataFiles\Apps 2728 exports"
)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

today = datetime.now(ZoneInfo("America/New_York")).date()
export = (
    df.with_columns(pl.col("date_submitted").dt.date().alias("submitted_day"))
    .filter(pl.col("submitted_day") < today)
    .drop("date_submitted", "id")
)

for (day,), day_df in export.group_by("submitted_day", maintain_order=True):
    path = EXPORT_DIR / f"apps_2728_{day:%Y-%m-%d}.csv"
    if path.exists():
        path.unlink()
    day_df.drop("submitted_day").write_csv(path)
    print(f"wrote {path.name}: {day_df.height} rows")

# Combined file of every day above, replaced on each run
all_path = EXPORT_DIR / "apps_2728_all.csv"
if all_path.exists():
    all_path.unlink()
export.drop("submitted_day").write_csv(all_path)
print(f"wrote {all_path.name}: {export.height} rows")
