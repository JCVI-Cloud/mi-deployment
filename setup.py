#!/usr/bin/env python 
"""
"""
from setuptools import setup, find_packages

setup(name="mi-deployment",
      version="0.1",
      description="Set of fabric scripts used to automate the configuring and and deployment of Galaxy.",
      author="Enis Afgan",
      packages=["mi_deployment", "mi_deployment.util", "mi_deployment.tools"],
      package_dir = {
        "mi_deployment": ".",
        "mi_deployment.util": "./util",
        "mi_deployment.tools": "./tools",
      },
      url="https://bitbucket.org/afgane/mi-deployment/",
      classifiers=[
        'Environment :: Console',
        'Intended Audience :: System Administrators',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7'])
      
