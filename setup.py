#!/usr/bin/env python 
"""
"""
from setuptools import setup, find_packages
from distutils.command.build import build as distutils_build
from os import utime, makedirs
from os.path import join, isdir
from shutil import copytree, copy, rmtree

## Setup python package with needed module structure in lib directory.

lib_dir="lib"

def create_init_file(directory):
    target_dir = join(lib_dir, directory)
    init_path = join(target_dir, '__init__.py')
    with file(init_path, 'a'):
        utime(init_path, None)

rmtree(lib_dir)
makedirs(join(lib_dir, "mi_deployment"))
create_init_file("mi_deployment")
package_files = ['data_fabfile.py', 
                 'mi_fabfile.py', 
                 'tools_fabfile.py', 
                 'volume_manipulations_fab.py', 
                 'util', 
                 'tools']

for file_to_copy in package_files:
    if isdir(file_to_copy):
        copy_func = copytree
    else:
        copy_func = copy
    copy_func(file_to_copy, join("lib", "mi_deployment", file_to_copy))

create_init_file(join("mi_deployment", "tools"))


setup(name="mi-deployment",
      version="0.1",
      description="Set of fabric scripts used to automate the configuring and and deployment of Galaxy.",
      author="Enis Afgan",
      packages=["mi_deployment", 
                "mi_deployment.util", 
                "mi_deployment.tools",
                "mi_deployment.tools.util"],
      package_dir = {
        "mi_deployment": "lib/mi_deployment",
      },
      url="https://bitbucket.org/afgane/mi-deployment/",
      classifiers=[
        'Environment :: Console',
        'Intended Audience :: System Administrators',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7'])
      
