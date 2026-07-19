# HydroAtlas Local Deployment

This repository is the local-only HydroAtlas setup for a single machine.

## Runtime layout

- SQLite database: `hydroatlas.db`
- ERA5 NetCDF files: `storage/era5/`
- Cached/generated files: `storage/cache/`

## Start the project

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app
```

The app will create and use `hydroatlas.db` and the `storage/` directory automatically.

## Notes

- The existing FastAPI API and frontend-facing endpoints stay intact.
- ERA5 ingestion is still available through `python -m ingestion.era5.cli` if you configure CDS credentials.
