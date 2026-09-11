# Yamuna Mess — Railway + PostgreSQL Ready

## Deploy
1. Upload this project to GitHub.
2. In Railway, create a project from the GitHub repository.
3. Add a PostgreSQL service/database to the same Railway project.
4. Ensure the database connection variable is exposed to the web service as `DATABASE_URL`.
5. Add these variables to the web service:
   - `SECRET_KEY` = a long random secret
   - `ADMIN_USER` = your admin username
   - `ADMIN_PASS` = a strong admin password
6. Railway should use the included `Procfile` (`gunicorn app:app`).
7. Generate a public domain from Railway Networking.
8. Test `/health` and then `/admin/login`.

## Important
The app automatically creates the PostgreSQL tables on first startup.
Do NOT use the demo credentials in production.
For production, use PostgreSQL (not SQLite), HTTPS, a strong secret/password, and regular database backups.

## Features
Student registration, student lookup, four meal slots, menu management, attendance, payments, admin dashboard, JSON export, health check, Marathi/English mobile-friendly UI.
