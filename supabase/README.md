# Supabase setup

1. Create a Supabase project and run [the migration](migrations/20260901000000_call_center_schema.sql) in its SQL Editor.
2. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `.env.local` and Vercel Project Settings > Environment Variables.
3. Run `pip install -r requirements.txt`, then start locally with `run-local.cmd`.

To move the existing Google Sheets records before cutover, install `pip install -r requirements-migration.txt` and run `python scripts/import_google_sheets.py` once. It reads the existing Google environment variables locally and sends data only to the configured Supabase project.

The service role key is used only by FastAPI and must never be placed in browser code. This migration blocks direct browser access to the data tables; the existing API remains the database boundary.