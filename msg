./venv/bin/python manage.py makemessages -l en -l sv -l fi -i '*venv*' -i '*web/static*' -i '*/data/*' -i '*.txt' -i '*uploads/*' -i '*downloads/*'
cd jbank/locale
../../venv/bin/python ../../manage.py compilemessages
