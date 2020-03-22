django-jbank
============

Basic Finnish bank file format support for Django projects. Django 3.0 support and unit test coverage 49%.

Features
========

* Parsing TO and camt.053.001.02 files
* Parsing SVM files
* Parsing pain.002.001.03 files
* Generating pain.001.001.03 files


Pre-Requisities
===============

* sudo apt install -y xmlsec1 libxmlsec1-dev libssl-dev libffi-dev


Install
=======

* pip install django-jbank


Tested Banks
============

* POP-Pankki
* Danskebank Finland
* Säästöpankki


Changes
=======

3.2.0:
+ Django 3.0 support, prospector fixes
