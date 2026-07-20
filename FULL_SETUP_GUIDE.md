# Full Setup Guide: HydroAtlas

This document provides a comprehensive guide on setting up the database, running the frontend and backend, downloading ERA5 data, and configuring environment variables.

---

## 1. Prerequisites & Virtual Environment

Ensure you have Python and Node.js installed. Open PowerShell and activate the virtual environment at the root of the project:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned ; & .\.venv\Scripts\Activate.ps1
```
*(If your absolute path is different, run: `(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& c:\Users\rudra\HydroAtlas\.venv\Scripts\Activate.ps1)`)*

---

## 2. Database Setup

The backend uses SQLite and Alembic for database migrations. Navigate to the `backend` directory to set up the DB:

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
```

This will create `hydroatlas.db` and apply all necessary tables.

---

## 3. Running the Backend

While inside the `backend` directory with the virtual environment activated, start the API:

```bash
uvicorn main:app --reload
```
The backend API will be available at `http://localhost:8000`.

---

## 4. Running the Frontend

Open a **new terminal**, navigate to the `frontend` directory, install the Node dependencies, and start the development server:

```bash
cd frontend
npm install
npm run dev
```
The frontend should now be accessible (usually at `http://localhost:5173` or similar based on Vite output).

---

## 5. ERA5 Downloader Setup and Configuration

The ERA5 data ingestion is handled by the backend. It requires valid Copernicus CDS API credentials.

### How to change the months to download
In the `backend` directory, open the `.env` file. You will see a setting called `ERA5_BOOTSTRAP_MONTHS`:

```ini
ERA5_BOOTSTRAP_MONTHS=2
```

- Change the `2` to however many months of historical data you wish to download.
- Ensure your `CDSAPI_URL` and `CDSAPI_KEY` are also correctly configured in this `.env` file.

### Running the Downloader
With the `.env` file configured and your virtual environment activated, navigate to the `backend` folder and run the ingestion CLI:

```bash
cd backend
python -m ingestion.era5.cli
```
This will start the downloading process based on the number of months specified in your `.env` configuration.
