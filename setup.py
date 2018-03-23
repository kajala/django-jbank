# -*- coding: utf-8 -*-
from distutils.core import setup
from setuptools import find_packages
from pip.req import parse_requirements

reqs = parse_requirements('requirements.txt', session=False)
install_requires = [str(ir.req) for ir in reqs if str(ir.req) != 'None']

setup(
    name='django-jbank',
    version='1.0.4',
    author=u'Jani Kajala',
    author_email='kajala@gmail.com',
    packages=find_packages(exclude=['project', 'venv']),
    include_package_data=True,
    url='',
    license='MIT licence, see LICENCE.txt',
    description='Finnish bank file format support for Django projects',
    long_description=open('README.md').read(),
    zip_safe=True,
    install_requires=install_requires
)
