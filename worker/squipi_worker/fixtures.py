"""A fictional programme-evaluation dataset for demos and tests. Deterministic, no real people,
no real places. Households in made-up districts, observed over three survey rounds, some of
them enrolled in a (fictional) cash-transfer programme at round 2."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DISTRICTS = ["Wadi Noor", "Halba Heights", "Marigold Flats", "Juniper Bend", "Sable Reach", "Quill Harbour"]


def fictional_programme(path: Path, households: int = 400, rounds: int = 3, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    hh = np.arange(1001, 1001 + households)
    district = rng.choice(DISTRICTS, households, p=[0.22, 0.18, 0.2, 0.15, 0.15, 0.10])
    treated = rng.random(households) < 0.5
    base_income = rng.lognormal(mean=7.4, sigma=0.45, size=households)
    rows = []
    for r in range(1, rounds + 1):
        date = pd.Timestamp("2023-03-01") + pd.DateOffset(months=6 * (r - 1))
        for i in range(households):
            post = r >= 2
            effect = 0.12 if (treated[i] and post) else 0.0
            inc = base_income[i] * (1 + 0.03 * (r - 1)) * (1 + effect) * rng.lognormal(0, 0.12)
            school = min(1.0, max(0.0, 0.62 + (0.08 if treated[i] and post else 0) + rng.normal(0, 0.12)))
            rows.append({
                "household_id": int(hh[i]),
                "survey_date": date.strftime("%Y-%m-%d"),
                "round": r,
                "district": district[i] if rng.random() > 0.01 else district[i].lower(),   # a few case variants
                "treated": int(treated[i]),
                "post": int(post),
                "hh_size": int(rng.integers(1, 9)),
                "monthly_income": round(float(inc), 2) if rng.random() > 0.06 else None,     # some missing
                "school_attendance_rate": round(float(school), 3),
                "food_expenditure_pct": round(float(min(95, max(5, rng.normal(42, 9)))), 1),
                "enumerator_code": f"EN{rng.integers(1, 15):02d}",
                "notes": rng.choice(["", "", "", "respondent absent, proxy used", "income self-reported", "revisit"]),
            })
    df = pd.DataFrame(rows)
    # a handful of exact duplicate rows, as happens with double entry
    df = pd.concat([df, df.sample(5, random_state=1)], ignore_index=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
