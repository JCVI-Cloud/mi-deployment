"""Fabric deployment file to set up a range of NGS tools

Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f tools_fabfile.py -i full_path_to_private_key_file -H servername install_tools
"""
import os
from contextlib import contextmanager

from fabric.api import *
from fabric.contrib.files import *

# -- Host specific setup for various groups of servers.

env.user = 'ubuntu'
env.remove_old_genomes = False
env.use_sudo = False

def amazon_ec2():
    """Setup for a ubuntu 10.04 on EC2

    NOTE: This script/environment assumes given environment directories are avilable.
    Typically, this would assume starting an EC2 instance, attaching an EBS
    volume to it, creating a file system on it, and mounting it at below paths.
    """
    env.user = 'ubuntu'
    env.path = '/mnt/galaxyTools/tools/pkg'
    env.install_dir = '/mnt/galaxyTools/tools'
    env.tmp_dir = "/mnt"
    env.galaxy_files = '/mnt/galaxyTools/tools'
    env.shell = "/bin/bash -l -c"
    env.use_sudo = True

# -- Fabric instructions

def install_tools():
    """Deploy a Galaxy server along with associated data files.
    """
    amazon_ec2()
    if not exists(env.install_dir):
        sudo("mkdir -p %s" % env.install_dir)
    append("export PATH=%s/bin:$PATH" % env.install_dir, "/etc/bash.bashrc", use_sudo=True)
    
    # _required_packages()
    # _required_libraries()
    # _support_programs()
    _install_ngs_tools()
    
    sudo("chown --recursive galaxy:galaxy %s" % os.path.split(env.install_dir)[0])

# == Decorators and context managers

def _if_not_installed(pname):
    def argcatcher(func):
        def decorator(*args, **kwargs):
            with settings(
                    hide('warnings', 'running', 'stdout', 'stderr'),
                    warn_only=True):
                result = run(pname)
            if result.return_code == 127:
                return func(*args, **kwargs)
        return decorator
    return argcatcher

def _if_installed(pname):
    """Run if the given program name is installed.
    """
    def argcatcher(func):
        def decorator(*args, **kwargs):
            with settings(
                    hide('warnings', 'running', 'stdout', 'stderr'),
                    warn_only=True):
                result = run(pname)
            if result.return_code not in [127]:
                return func(*args, **kwargs)
        return decorator
    return argcatcher

@contextmanager
def _make_tmp_dir():
    work_dir = os.path.join(env.tmp_dir, "fab_tmp")
    install_cmd = sudo if env.use_sudo else run
    if not exists(work_dir):
        install_cmd("mkdir %s" % work_dir)
        install_cmd("chown %s %s" % (env.user, work_dir))
    yield work_dir
    if exists(work_dir):
        install_cmd("rm -rf %s" % work_dir)

def _required_packages():
    """Install needed packages using apt-get"""
    packages = ['xfsprogs', # if not done by hand, required for EBS file system
                'unzip', 
                'gcc', 
                'g++',
                'pkg-config', # required by fastx-toolkit
                'zlib1g-dev', # required by bwa
                'libncurses5-dev' ]# required by SAMtools
    for package in packages:
        sudo("apt-get -y --force-yes install %s" % package)

# == NGS

def _install_ngs_tools():
    """Install external next generation sequencing tools.
    """
    _install_bowtie()
    _install_bwa()
    _install_samtools()
    # _install_fastx_toolkit()
    _install_maq()
    _install_bfast()
    _install_abyss()
    _install_R()
    _install_ucsc_tools()

def _install_R():
    version = "2.11.1"
    url = "http://mira.sunsite.utk.edu/CRAN/src/base/R-2/R-%s.tar.gz" % version
    install_dir = os.path.join(env.install_dir, "r_%s" % version)
    with _make_tmp_dir() as work_dir:
        with contextlib.nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            run("tar xvzf %s" % os.path.split(url)[1])
            with cd("R-%s" % version):
                run("./configure --prefix=%s --enable-R-shlib --with-x=no --with-readline=no" % install_dir)
                with settings(hide('stdout')):
                    print "Making R..."
                    sudo("make")
                    sudo("make install")
                    sudo("cd %s; stow r_%s" % (env.install_dir, version))
                print "----- R installed to %s -----" % install_dir

@_if_not_installed("faToTwoBit")
def _install_ucsc_tools():
    """Install useful executables from UCSC.
    """
    tools = ["liftOver", "faToTwoBit"]
    url = "http://hgdownload.cse.ucsc.edu/admin/exe/linux.x86_64/"
    install_dir = os.path.join(env.install_dir, "bin")
    for tool in tools:
        with cd(install_dir):
            if not exists(tool):
                install_cmd = sudo if env.use_sudo else run
                install_cmd("wget %s%s" % (url, tool))
                install_cmd("chmod a+rwx %s" % tool)

def _install_ucsc_tools_src():
    """Install Jim Kent's executables from source.
    """
    url = "http://hgdownload.cse.ucsc.edu/admin/jksrc.zip"
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)

@_if_not_installed("bowtie")
def _install_bowtie():
    """Install the bowtie short read aligner.
    """
    version = "0.12.5"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/bowtie-bio/bowtie/%s/" \
          "bowtie-%s-src.zip" % (version, version)
    install_dir = os.path.join(env.install_dir, "bin")
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s" % (url, mirror_info))
            run("unzip %s" % os.path.split(url)[-1])
            with cd("bowtie-%s" % version):
                run("make")
                for fname in run("find -perm -100 -name 'bowtie*'").split("\n"):
                    install_cmd("mv -f %s %s" % (fname, install_dir))

@_if_not_installed("bwa")
def _install_bwa():
    version = "0.5.7"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/bio-bwa/bwa-%s.tar.bz2" % (
            version)
    install_dir = os.path.join(env.install_dir, "bin")
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s" % (url, mirror_info))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            with cd("bwa-%s" % version):
                run("make")
                install_cmd("mv bwa %s" % install_dir)
                install_cmd("mv solid2fastq.pl %s" % install_dir)
                install_cmd("mv qualfa2fq.pl %s" % install_dir)

@_if_not_installed("samtools")
def _install_samtools():
    version = "0.1.7"
    vext = "a"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/samtools/samtools/%s/" \
            "samtools-%s%s.tar.bz2" % (version, version, vext)
    install_dir = os.path.join(env.install_dir, "bin")
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s" % (url, mirror_info))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            with cd("samtools-%s%s" % (version, vext)):
                run("sed -i.bak -r -e 's/-lcurses/-lncurses/g' Makefile")
                #sed("Makefile", "-lcurses", "-lncurses")
                run("make")
                for install in ["samtools", "misc/maq2sam-long"]:
                    install_cmd("mv -f %s %s" % (install, install_dir))

@_if_not_installed("fastq_quality_boxplot_graph.sh")
def _install_fastx_toolkit():
    version = "0.0.13"
    gtext_version = "0.6"
    url_base = "http://hannonlab.cshl.edu/fastx_toolkit/"
    fastx_url = "%sfastx_toolkit-%s.tar.bz2" % (url_base, version)
    gtext_url = "%slibgtextutils-%s.tar.bz2" % (url_base, gtext_version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % gtext_url)
            run("tar -xjvpf %s" % (os.path.split(gtext_url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("libgtextutils-%s" % gtext_version):
                run("./configure --prefix=%s" % (env.install_dir))
                run("make")
                install_cmd("make install")
            run("wget %s" % fastx_url)
            run("tar -xjvpf %s" % os.path.split(fastx_url)[-1])
            with cd("fastx_toolkit-%s" % version):
                run("export PKG_CONFIG_PATH=%s/lib; ./configure --prefix=%s" % (env.install_dir, env.install_dir))
                run("make")
                install_cmd("make install")

@_if_not_installed("maq")
def _install_maq():
    version = "0.7.1"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/maq/maq/%s/maq-%s.tar.bz2" \
            % (version, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s" % (url, mirror_info))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("maq-%s" % version):
                run("./configure --prefix=%s" % (env.install_dir))
                run("make")
                install_cmd("make install")

@_if_not_installed("bfast")
def _install_bfast():
    version = "0.6.4"
    vext = "d"
    url = "http://downloads.sourceforge.net/project/bfast/bfast/%s/bfast-%s%s.tar.gz"\
            % (version, version, vext)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % (url))
            run("tar -xzvpf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("bfast-%s%s" % (version, vext)):
                run("./configure --prefix=%s" % (env.install_dir))
                run("make")
                install_cmd("make install")

@_if_not_installed("ABYSS")
def _install_abyss():
    version = "1.2.2"
    url = "http://www.bcgsc.ca/downloads/abyss/abyss-%s.tar.gz" % version
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % (url))
            run("tar -xvzf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("abyss-%s" % version):
                run("./configure --prefix=%s --with-mpi=/opt/galaxy/pkg/openmpi" % env.install_dir)
                run("make")
                install_cmd("make install")
    
def _required_libraries():
    """Install galaxy libraries not included in the eggs.
    """
    # -- HDF5
    # wget 'http://www.hdfgroup.org/ftp/HDF5/current/src/hdf5-1.8.4-patch1.tar.bz2'
    # tar -xjvpf hdf5-1.8.4-patch1.tar.bz2
    # ./configure --prefix=/source
    # make && make install
    #
    # -- PyTables http://www.pytables.org/moin
    # wget 'http://www.pytables.org/download/preliminary/pytables-2.2b3/tables-2.2b3.tar.gz'
    # tar -xzvpf tables-2.2b3.tar.gz
    # cd tables-2.2b3
    # python2.6 setup.py build --hdf5=/source
    # python2.6 setup.py install --hdf5=/source
    pass

def _support_programs():
    """Install programs used by galaxy.
    """
    pass
    # gnuplot
    # gcc44-fortran
    # R
    # rpy
    # easy_install gnuplot-py
    # emboss

