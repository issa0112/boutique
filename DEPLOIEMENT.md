# Deploiement du projet Boutique

## 1) Preparation locale

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copier `.env.example` vers `.env` puis adapter les valeurs.

```powershell
Copy-Item .env.example .env
```

## 2) Variables de production minimales

- `DJANGO_SECRET_KEY`: cle secrete forte
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`: ex `mon-app.onrender.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS`: ex `https://mon-app.onrender.com`
- `DATABASE_URL`: URL PostgreSQL en production

## 3) Deploiement Render

- Build command:
```bash
pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput
```

- Start command:
```bash
gunicorn boutique.wsgi:application --log-file -
```

- Ajouter les variables d'environnement ci-dessus dans Render.

## 4) Deploiement VPS (Ubuntu + Nginx)

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn boutique.wsgi:application --bind 0.0.0.0:8000
```

Configurer ensuite un service `systemd` pour gunicorn et un reverse proxy Nginx.
