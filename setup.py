# -*- coding: utf-8 -*-
from distutils.core import setup
from setuptools import find_packages  # type: ignore


def parse_requirements(filename, session=False):
    """load requirements from a pip requirements file"""
    lineiter = (line.strip() for line in open(filename))
    return [line for line in lineiter if line and not line.startswith("#")]


install_requires = parse_requirements("requirements.txt", session=False)

setup(
    name="django-jbank",
    version="4.7.3",
    author="Jani Kajala",
    author_email="kajala@gmail.com",
    packages=find_packages(exclude=["project", "venv"]),
    include_package_data=True,
    url="",
    license="MIT licence, see LICENCE.txt",
    description="Finnish bank file format support for Django projects",
    long_description=open("README.md").read(),
    zip_safe=False,
    install_requires=install_requires,
)
