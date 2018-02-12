python manage.py makemessages -i venv -i data
cd jbank/locale
python ../../manage.py compilemessages
cd ..
