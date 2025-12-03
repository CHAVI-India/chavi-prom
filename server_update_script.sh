#!/bin/bash

# Change to the directory

cd /var/www/chavi-prom

echo "Changed to Taret Directory"

# Pull from repository

git pull

echo "Repository Updated"

# Activate virtual environment

source /var/www/chavi-prom/venv/bin/activate
echo "Activated Virtual Environment"

# Install dependencies

echo "Installing Dependencies"
pip install -r requirements.txt

# Migrate database

echo "Migrating Database"
python manage.py migrate

# Run NPM build

echo "Running NPM build for Tailwind CSS"
npm run tailwind:build 

# Collect static files

echo "Collecting Static Files"
python manage.py collectstatic --noinput

# Restart Gunicorn installed using Supervisor

echo "Restarting Gunicorn"
sudo supervisorctl restart chavi-prom_gunicorn

