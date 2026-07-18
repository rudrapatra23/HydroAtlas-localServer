# HydroAtlas - Local Server

This is the local machine setup for HydroAtlas. It replaces cloud-based dependencies with local equivalents for development and offline execution.

## Changes in this Branch
- **Storage**: We use a local directory (`storage` at the root of the project) instead of AWS S3. Era5Land data will be stored here.
- **Database**: We use a local PostgreSQL instance via Docker Compose instead of Neon DB.
- **Parallel Computing**: Where possible, CPU-bound tasks are adapted to use parallel computing strategies.

## How to Run

1. **Start the database and services**:
   Ensure Docker is installed and running.
   ```bash
   docker compose up --build -d postgres
   ```
   Wait for the Postgres database to be ready.

2. **Run migrations and start everything**:
   ```bash
   docker compose up --build
   ```

   This will:
   - Start the local PostgreSQL database (`postgres` service)
   - Run the Alembic migrations (`migrate` service)
   - Start the FastAPI backend on port 8000 (`backend` service)
   - Run the ERA5 ingestion pipeline (`era5-scheduler` service)
   - Start the React frontend on port 80 (`frontend` service)

3. **Access the application**:
   Open your browser and navigate to `http://localhost`.

## Architecture Note
The local storage adapter stores data inside `storage/` and replicates the AWS S3 `StoragePort` interface. 
This branch is maintained in the `hydroatlaslocalserver` repository.
