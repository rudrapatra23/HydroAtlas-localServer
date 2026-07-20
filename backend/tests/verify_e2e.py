"""End-to-end verification against the live postgres database."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main() -> int:
    from infrastructure.db.session import async_session_maker
    from infrastructure.repositories.postgres_dataset_repository import PostgresDatasetRepository

    errors = 0
    variables = ["precipitation", "soil_moisture", "surface_runoff"]

    async with async_session_maker() as session:
        repo = PostgresDatasetRepository(session)

        # --- Test 1: Each variable returns exactly one asset (no MultipleResultsFound) ---
        for var in variables:
            try:
                asset = await repo.get_by_period(
                    year=2024, month=1, provider="era5-land", variable=var
                )
                if asset is not None:
                    print(f"PASS  get_by_period(variable='{var}') -> asset id={asset.id}, "
                          f"storage_key={asset.storage_key}")
                else:
                    print(f"WARN  get_by_period(variable='{var}') -> None (no asset in DB for 2024-01)")
            except Exception as e:
                print(f"FAIL  get_by_period(variable='{var}') raised: {e}")
                errors += 1

        # --- Test 2: variable is now required (str, not Optional) ---
        import inspect
        sig = inspect.signature(repo.get_by_period)
        param = sig.parameters.get("variable")
        if param is None:
            print("FAIL  get_by_period has no 'variable' parameter")
            errors += 1
        elif param.default is inspect.Parameter.empty:
            print("PASS  get_by_period 'variable' parameter is required (no default)")
        else:
            print(f"FAIL  get_by_period 'variable' has default={param.default} (should be required)")
            errors += 1

        # --- Test 3: Verify the SQL includes variable in WHERE ---
        # We do this by calling with a non-existent variable and confirming None is returned
        # (if the filter weren't applied, it would return one of the real assets or raise MultipleResultsFound)
        try:
            result = await repo.get_by_period(
                year=2024, month=1, provider="era5-land", variable="nonexistent_variable"
            )
            if result is None:
                print("PASS  get_by_period(variable='nonexistent_variable') -> None (filter applied)")
            else:
                print(f"FAIL  get_by_period(variable='nonexistent_variable') returned asset: {result.id}")
                errors += 1
        except Exception as e:
            print(f"FAIL  get_by_period(variable='nonexistent_variable') raised: {e}")
            errors += 1

    print()
    if errors == 0:
        print("ALL CHECKS PASSED")
    else:
        print(f"{errors} CHECK(S) FAILED")
    return errors


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
