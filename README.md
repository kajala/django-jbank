django-jbank
============

Basic (Finnish) bank file format support for Django projects. Django 4 support and unit test coverage 51%.

Features
========

* Finnish banks:
  * Parsing TO/TITO files
  * Parsing SVM/KTL files
  * Parsing camt.053.001.02 files
  * Parsing camt.054.001.02 files
  * Parsing pain.002.001.03 files
  * Generating pain.001.001.03 files
* Spanish banks:
  * Parsing AEB43 statement files


Pre-Requisites
==============

* sudo apt install -y xmlsec1 libxmlsec1-dev libssl-dev libffi-dev


Install
=======

* pip install django-jbank


Tested Banks
============

* POP-Pankki
* Danskebank Finland
* Säästöpankki
* Aktia
* Nordea


Converting p12 to PEM formats
=============================

1. openssl pkcs12 -in WSNDEA1234.p12 -out WSNDEA1234.pem
2. openssl rsa -in WSNDEA1234.pem -outform PEM -out prod_private_key.pem
3. openssl x509 -in WSNDEA1234.pem -outform PEM -out prod_public_key_cert.pem


Changes
=======

4.0.1:
+ Upgraded component requirements to latest lxml (4.9.1), etc.

3.2.0:
+ Django 3.0 support, prospector fixes
