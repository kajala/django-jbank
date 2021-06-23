django-jbank
============

Basic Finnish bank file format support for Django projects. Django 3.0 support and unit test coverage 51%.

Features
========

* Finnish banks:
  * Parsing TO/TITO files
  * Parsing SVM/KTL files
  * Parsing camt.053.001.02 files
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


Changes
=======

3.2.0:
+ Django 3.0 support, prospector fixes
