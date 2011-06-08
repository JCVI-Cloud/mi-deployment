"""Fabric deployment file to set up a range of NGS tools

Fabric (http://docs.fabfile.org) is used to manage the automation of
a remote server.

Usage:
    fab -f tools_fabfile.py -i full_path_to_private_key_file -H <servername> install_tools
"""
# for Python 2.5
from __future__ import with_statement

import os
import datetime as dt
from contextlib import contextmanager, nested

# from fabric.api import *
# from fabric.contrib.files import *
from fabric.api import sudo, run, env, cd
from fabric.contrib.files import exists, settings, hide, append
from fabric.colors import green, yellow

# -- Adjust this link if using content from another location
CDN_ROOT_URL = "http://userwww.service.emory.edu/~eafgan/content"

# -- Host specific setup for various groups of servers.
env.user = 'ubuntu'
env.use_sudo = False

def amazon_ec2():
    """Setup for a ubuntu 10.04 on EC2

    NOTE: This script/environment assumes given environment directories are available.
    Typically, this would assume starting an EC2 instance, attaching an EBS
    volume to it, creating a file system on it, and mounting it at below paths.
    """
    env.user = 'ubuntu'
    env.install_dir = '/mnt/galaxyTools/tools'
    env.galaxy_home = '/mnt/galaxyTools/galaxy-central'
    env.tmp_dir = "/mnt"
    env.shell = "/bin/bash -l -c"
    env.use_sudo = True

# -- Fabric instructions

def install_tools():
    """Deploy a Galaxy server along with associated data files.
    """
    _check_version()
    time_start = dt.datetime.utcnow()
    print(yellow("Configuring host '%s'. Start time: %s" % (env.hosts[0], time_start)))
    amazon_ec2()
    if not exists(env.install_dir):
        sudo("mkdir -p %s" % env.install_dir)
    append("/etc/bash.bashrc", "export PATH=PATH=%s/bin:$PATH" % env.install_dir, use_sudo=True)
    _required_packages()
    # _required_libraries() # currently, nothing there
    # _support_programs() # currently, nothing there
    _install_tools()
    _install_galaxy()
    sudo("chown --recursive galaxy:galaxy %s" % os.path.split(env.install_dir)[0])
    time_end = dt.datetime.utcnow()
    print(yellow("Duration of tools installation: %s" % str(time_end-time_start)))

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

def _install_galaxy():
    is_new = False
    install_cmd = sudo if env.use_sudo else run
    if not exists(env.galaxy_home):
        is_new = True
        with cd(os.path.split(env.galaxy_home)[0]):
            install_cmd('hg clone http://bitbucket.org/galaxy/galaxy-central/')
    with cd(env.galaxy_home):
        if not is_new:
            install_cmd('hg pull')
            install_cmd('hg update')
            install_cmd('sh manage_db.sh upgrade')
        # Make sure Galaxy runs in a new shell and does not inherit the environment
        # by adding the '-ES' flag to all invocations of python within run.sh
        with settings(warn_only=True):
            install_cmd("sed -i 's/python .\//python -ES .\//g' run.sh")
        # Append DRMAA_LIBRARY_PATH in run.sh as well (this file will exist
        # once SGE is installed - which happens at instance contextualization)
        with settings(warn_only=True):
            install_cmd("grep -q 'export DRMAA_LIBRARY_PATH=/opt/sge/lib/lx24-amd64/libdrmaa.so.1.0' run.sh; if [ $? -eq 1 ]; then sed -i '2 a export DRMAA_LIBRARY_PATH=/opt/sge/lib/lx24-amd64/libdrmaa.so.1.0' run.sh; fi")
            # Upload the custom cloud welcome screen files
            if not exists("%s/static/images/cloud.gif" % env.galaxy_home):
                sudo("wget --output-document=%s/static/images/cloud.gif %s/cloud.gif" % (env.galaxy_home, CDN_ROOT_URL))
            if not exists("%s/static/images/cloud_txt.png" % env.galaxy_home):
                sudo("wget --output-document=%s/static/images/cloud_text.png %s/cloud_text.png" % (env.galaxy_home, CDN_ROOT_URL))
            sudo("wget --output-document=%s/static/welcome.html %s/welcome.html" % (env.galaxy_home, CDN_ROOT_URL))
        # set up the symlink for SAMTOOLS (remove this code once SAMTOOLS is converted to data tables)
        if exists("%s/tool-data/sam_fa_indices.loc" % env.galaxy_home):
            install_cmd("rm %s/tool-data/sam_fa_indices.loc" % env.galaxy_home)
        tmp_loc = False
        if not exists("/mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc"):
            install_cmd("touch /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc")
            tmp_loc = True
        install_cmd("ln -s /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc %s/tool-data/sam_fa_indices.loc" % env.galaxy_home)
        if tmp_loc:
            install_cmd("rm /mnt/galaxyIndices/galaxy/tool-data/sam_fa_indices.loc")
        # set up the special HYPHY link in tool-data/
        hyphy_dir = os.path.join(env.install_dir, 'hyphy', 'default')
        install_cmd('ln -s %s tool-data/HYPHY' % hyphy_dir)
        # set up the jars directory
        if not exists('tool-data/shared/jars'):
            install_cmd("mkdir -p tool-data/shared/jars")
        srma_dir = os.path.join(env.install_dir, 'srma', 'default')
        haploview_dir = os.path.join(env.install_dir, 'haploview', 'default')
        picard_dir = os.path.join(env.install_dir, 'picard', 'default')
        install_cmd('ln -s %s/srma.jar tool-data/shared/jars/.' % srma_dir)
        install_cmd('ln -s %s/haploview.jar tool-data/shared/jars/.' % haploview_dir)
        install_cmd('ln -s %s/*.jar tool-data/shared/jars/.' % picard_dir)

# == NGS

def _install_tools():
    """Install external tools (galaxy tool dependencies).
    """
    _install_bowtie()
    _install_bwa()
    _install_samtools()
    # _install_fastx_toolkit()
    _install_maq()
    _install_bfast()
    _install_abyss()
    # _install_R()
    # _install_rpy()
    _install_ucsc_tools()
    _install_velvet()
    _install_macs()
    _install_tophat()
    _install_cufflinks()
    _install_megablast()
    _install_blast()
    _install_sputnik()
    _install_taxonomy()
    _install_add_scores()
    _install_emboss_phylip()
    _install_hyphy()
    _install_lastz()
    _install_perm()
    _install_gatk()
    _install_srma()
    _install_beam()
    _install_pass()
    _install_lps_tool()
    _install_plink()
    # _install_fbat() # Not available
    _install_haploview()
    _install_eigenstrat()
    _install_mosaik()
    _install_freebayes()
    _install_picard()

def _install_R():
    version = "2.11.1"
    url = "http://mira.sunsite.utk.edu/CRAN/src/base/R-2/R-%s.tar.gz" % version
    pkg_name = 'r'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with nested(cd(work_dir), settings(hide('stdout'))):
            run("wget %s" % url)
            run("tar xvzf %s" % os.path.split(url)[1])
            with cd("R-%s" % version):
                run("./configure --prefix=%s --enable-R-shlib --with-x=no --with-readline=no" % install_dir)
                with settings(hide('stdout')):
                    print(yellow("Making R..."))
                    sudo("make")
                    sudo("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- R %s installed to %s -----" % (version, install_dir)))

def _install_rpy():
    # *Does not work in reality*
    version = '1.0.3'
    url = 'http://downloads.sourceforge.net/project/rpy/rpy/%s/rpy-%s.tar.gz' % (version, version)
    mirror_info = '?use_mirror=surfnet'
    pkg_name = 'rpy'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("tar -xvzf %s" % os.path.split(url)[-1])
            install_cmd = sudo if env.use_sudo else run
            with cd("rpy-%s" % version):
                install_cmd("python setup.py install --prefix %s" % install_dir)
                # TODO: include prefix location into PYTHONPATH as part of env.sh:
                # (e.g., "%s/lib/python2.6/site-packages/rpy-1.0.3-py2.6.egg-info" % install_dir)
            print(green("----- RPy %s installed to %s -----" % (version, install_dir)))

def _install_ucsc_tools():
    """Install useful executables from UCSC.
    """
    from datetime import date
    version = date.today().strftime('%Y%m%d')
    url = "http://hgdownload.cse.ucsc.edu/admin/exe/linux.x86_64/"
    pkg_name = 'ucsc_tools'
    tools = ["liftOver", "twoBitToFa", "wigToBigWig"]
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    for tool in tools:
        with cd(install_dir):
            if not exists(tool):
                install_cmd = sudo if env.use_sudo else run
                install_cmd("wget %s%s" % (url, tool))
                install_cmd("chmod 755 %s" % tool)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- UCSC Tools installed to %s -----" % install_dir))

def _install_ucsc_tools_src():
    """Install Jim Kent's executables from source.
    """
    url = "http://hgdownload.cse.ucsc.edu/admin/jksrc.zip"
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)

# @_if_not_installed("bowtie")
def _install_bowtie():
    """Install the bowtie short read aligner."""
    version = "0.12.7"
    mirror_info = "?use_mirror=cdnetworks-us-2"
    url = "http://downloads.sourceforge.net/project/bowtie-bio/bowtie/%s/bowtie-%s-src.zip" % (version, version)
    pkg_name = 'bowtie'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("unzip %s" % os.path.split(url)[-1])
            with cd("bowtie-%s" % version):
                run("make")
                for fname in run("find -perm -100 -name 'bowtie*'").split("\n"):
                    install_cmd("mv -f %s %s" % (fname, install_dir))
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- bowtie %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("bwa")
def _install_bwa():
    version = "0.5.7"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/bio-bwa/bwa-%s.tar.bz2" % (
            version)
    pkg_name = 'bwa'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            with cd("bwa-%s" % version):
                run("make")
                install_cmd("mv bwa %s" % install_dir)
                install_cmd("mv solid2fastq.pl %s" % install_dir)
                install_cmd("mv qualfa2fq.pl %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- BWA %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("samtools")
def _install_samtools():
    version = "0.1.12"
    vext = "a"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/samtools/samtools/%s/" \
            "samtools-%s%s.tar.bz2" % (version, version, vext)
    pkg_name = 'samtools'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            with cd("samtools-%s%s" % (version, vext)):
                run("sed -i.bak -r -e 's/-lcurses/-lncurses/g' Makefile")
                run("make")
                for install in ["samtools", "misc/maq2sam-long"]:
                    install_cmd("mv -f %s %s" % (install, install_dir))
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- SAMtools %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("fastq_quality_boxplot_graph.sh")
def _install_fastx_toolkit():
    version = "0.0.13"
    gtext_version = "0.6"
    url_base = "http://hannonlab.cshl.edu/fastx_toolkit/"
    fastx_url = "%sfastx_toolkit-%s.tar.bz2" % (url_base, version)
    gtext_url = "%slibgtextutils-%s.tar.bz2" % (url_base, gtext_version)
    pkg_name = 'fastx_toolkit'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % gtext_url)
            run("tar -xjvpf %s" % (os.path.split(gtext_url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("libgtextutils-%s" % gtext_version):
                run("./configure --prefix=%s" % (install_dir))
                run("make")
                install_cmd("make install")
            run("wget %s" % fastx_url)
            run("tar -xjvpf %s" % os.path.split(fastx_url)[-1])
            with cd("fastx_toolkit-%s" % version):
                run("export PKG_CONFIG_PATH=%s/lib; ./configure --prefix=%s" % (install_dir, install_dir))
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- FASTX Toolkit %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("maq")
def _install_maq():
    version = "0.7.1"
    mirror_info = "?use_mirror=cdnetworks-us-1"
    url = "http://downloads.sourceforge.net/project/maq/maq/%s/maq-%s.tar.bz2" \
            % (version, version)
    pkg_name = 'maq'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("tar -xjvpf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("maq-%s" % version):
                run("./configure --prefix=%s" % (install_dir))
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- MAQ %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("bfast")
def _install_bfast():
    version = "0.6.4"
    vext = "e"
    url = "http://downloads.sourceforge.net/project/bfast/bfast/%s/bfast-%s%s.tar.gz"\
            % (version, version, vext)
    pkg_name = 'bfast'
    install_dir = os.path.join(env.install_dir, pkg_name, "%s%s" % (version, vext))
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % (url))
            run("tar -xzvpf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("bfast-%s%s" % (version, vext)):
                run("./configure --prefix=%s" % (install_dir))
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- BFAST %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("ABYSS")
def _install_abyss():
    version = "1.2.5"
    url = "http://www.bcgsc.ca/downloads/abyss/abyss-%s.tar.gz" % version
    pkg_name = 'abyss'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("abyss-%s" % version):
                run("./configure --prefix=%s --with-mpi=/opt/galaxy/pkg/openmpi" % install_dir)
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- ABySS %s installed to %s -----" % (version, install_dir)))

def _install_velvet():
    version = "1.0.13"
    url = "http://www.ebi.ac.uk/~zerbino/velvet/velvet_%s.tgz" % version
    pkg_name = "velvet"
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd("velvet_%s" % version):
                run("make")
                for fname in run("find -perm -100 -name 'velvet*'").split("\n"):
                    install_cmd("mv -f %s %s" % (fname, install_dir))
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- Velvet %s installed to %s -----" % (version, install_dir)))

def _install_macs():
    version = "1.3.7.1"
    url = "http://liulab.dfci.harvard.edu/MACS/src/MACS-%s.tar.gz" % version
    pkg_name = "macs"
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget --user=macs --password=chipseq %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            install_cmd = sudo if env.use_sudo else run
            with cd("MACS-%s" % version):
                install_cmd("python setup.py install --prefix %s" % install_dir)
                # TODO: include prefix location into PYTHONPATH as part of env.sh:
                # (e.g., "%s/lib/python2.6/site-packages/MACS-1.3.7.1-py2.6.egg-info" % install_dir)
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("echo 'PYTHONPATH=%s/lib/python2.6/site-packages:$PYTHONPATH' >> %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- MACS %s installed to %s -----" % (version, install_dir)))

def _install_tophat():
    version = '1.2.0'
    url = 'http://tophat.cbcb.umd.edu/downloads/tophat-%s.Linux_x86_64.tar.gz' % version
    pkg_name = "tophat"
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd(os.path.split(url)[-1].split('.tar.gz')[0]):
                install_cmd("mv * %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- TopHat %s installed to %s -----" % (version, install_dir)))

def _install_cufflinks():
    version = '1.0.1'
    url = 'http://cufflinks.cbcb.umd.edu/downloads/cufflinks-%s.Linux_x86_64.tar.gz' % version
    pkg_name = "cufflinks"
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd(os.path.split(url)[-1].split('.tar.gz')[0]):
                install_cmd("mv * %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- Cufflinks %s installed to %s -----" % (version, install_dir)))

def _install_megablast():
    version = '2.2.22'
    url = 'ftp://ftp.ncbi.nlm.nih.gov/blast/executables/release/%s/blast-%s-x64-linux.tar.gz' % (version, version)
    pkg_name = 'blast'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd('blast-%s/bin' % version):
                    install_cmd("mv * %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- MEGABLAST %s installed to %s -----" % (version, install_dir)))

def _install_blast():
    version = '2.2.25+'
    url = 'ftp://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/%s/ncbi-blast-%s-x64-linux.tar.gz' % (version[:-1], version)
    pkg_name = 'blast'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd('ncbi-blast-%s/bin' % version):
                    install_cmd("mv * %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- BLAST %s installed to %s -----" % (version, install_dir)))

def _install_sputnik():
    version = 'r1'
    url = 'http://bitbucket.org/natefoo/sputnik-mononucleotide/downloads/sputnik_%s_linux2.6_x86_64' % version
    pkg_name = 'sputnik'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget -O sputnik %s" % url)
            install_cmd("mv sputnik %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh %s/sputnik" % (install_dir, install_dir))
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_taxonomy():
    version = 'r2'
    url = 'http://bitbucket.org/natefoo/taxonomy/downloads/taxonomy_%s_linux2.6_x86_64.tar.gz' % version
    pkg_name = 'taxonomy'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd(os.path.split(url)[-1].split('.tar.gz')[0]):
                install_cmd("mv * %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_add_scores():
    version = 'r1'
    url = 'http://bitbucket.org/natefoo/add_scores/downloads/add_scores_%s_linux2.6_x86_64' % version
    pkg_name = 'add_scores'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget -O add_scores %s" % url)
            install_cmd("mv add_scores %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh %s/add_scores" % (install_dir, install_dir))
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_emboss_phylip():
    version = '5.0.0'
    url = 'ftp://emboss.open-bio.org/pub/EMBOSS/old/%s/EMBOSS-%s.tar.gz' % (version, version)
    pkg_name = 'emboss'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd(os.path.split(url)[-1].split('.tar.gz')[0]):
                run("./configure --prefix=%s" % install_dir)
                run("make")
                install_cmd("make install")
    phylip_version = '3.6b'
    url = 'ftp://emboss.open-bio.org/pub/EMBOSS/old/%s/PHYLIP-%s.tar.gz' % (version, phylip_version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd(os.path.split(url)[-1].split('.tar.gz')[0]):
                run("./configure --prefix=%s" % install_dir)
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- EMBOSS+PHYLIP %s/%s installed to %s -----" % (version, phylip_version, install_dir)))

def _install_hyphy():
    revision = '418'
    version = 'r%s' % revision
    url = 'http://www.datam0nk3y.org/svn/hyphy'
    pkg_name = 'hyphy'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("svn co -r %s %s src" % (revision, url))
            run("mkdir -p build/Source/Link")
            run("mkdir build/Source/SQLite")
            run("cp src/trunk/Core/*.{h,cp,cpp} build/Source")
            run("cp src/trunk/HeadlessLink/*.{h,cpp} build/Source/SQLite")
            run("cp src/trunk/NewerFunctionality/*.{h,cpp} build/Source/")
            run("cp src/SQLite/trunk/*.{c,h} build/Source/SQLite/")
            run("cp src/trunk/Scripts/*.sh build/")
            run("cp src/trunk/Mains/main-unix.cpp build/Source/main-unix.cxx")
            run("cp src/trunk/Mains/hyphyunixutils.cpp build/Source/hyphyunixutils.cpp")
            run("cp -R src/trunk/{ChartAddIns,DatapanelAddIns,GeneticCodes,Help,SubstitutionClasses,SubstitutionModels,TemplateBatchFiles,TopologyInference,TreeAddIns,UserAddins} build")
            run("rm build/Source/preferences.cpp")
            with cd("build"):
                run("bash build.sh SP")
            install_cmd("mv build/* %s" % install_dir)
    sudo("touch %s/env.sh" % install_dir)
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- HYPHY %s installed to %s -----" % (version, install_dir)))

def _install_lastz():
    version = '1.01.88'
    url = 'http://www.bx.psu.edu/~rsharris/lastz/older/lastz-%s.tar.gz' % version
    pkg_name = 'lastz'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % os.path.split(url)[-1])
            with cd('lastz-distrib-%s' % version):
                run("sed -i -e 's/GCC_VERSION == 40302/GCC_VERSION >= 40302/' src/quantum.c")
                run("make")
                install_cmd("make LASTZ_INSTALL=%s install" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- LASTZ %s installed to %s -----" % (version, install_dir)))

def _install_perm():
    version = '3.0'
    url = 'http://perm.googlecode.com/files/PerM_Linux64%28noOpenMp%29.gz'
    pkg_name = 'perm'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget -O PerM.gz %s" % url)
            run("gunzip PerM.gz")
            install_cmd("mv PerM %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh %s/PerM" % (install_dir, install_dir))
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- PerM %s installed to %s -----" % (version, install_dir)))

def _install_gatk():
    version = '1.0.5777'
    url = 'ftp://ftp.broadinstitute.org/pub/gsa/GenomeAnalysisTK/GenomeAnalysisTK-%s.tar.bz2' % version
    pkg_name = 'gatk'
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
       install_cmd("mkdir -p %s" % install_dir)
       install_cmd("mkdir -p %s/bin" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget -O gatk.tar.bz2 %s" % url)
            run("tar -xjf gatk.tar.bz2")
            install_cmd("cp GenomeAnalysisTK-%s/GenomeAnalysisTK.jar %s/bin" % ( version, install_dir) )
    # Create shell script to wrap jar
    sudo("echo '#!/bin/sh' > %s/bin/gatk" % ( install_dir ) )
    sudo("echo 'java -jar %s/bin/GenomeAnalysisTK.jar $@' >> %s/bin/gatk" % ( install_dir, install_dir ) )
    sudo("chmod +x %s/bin/gatk" % install_dir)
    # env file
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    # default link
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))

def _install_srma():
    version = '0.1.15'
    mirror_info = "?use_mirror=voxel"
    url = 'http://downloads.sourceforge.net/project/srma/srma/%s/srma-%s.jar' \
            % (version[:3], version)
    pkg_name = 'srma'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            install_cmd("mv srma-%s.jar %s" % (version, install_dir))
            install_cmd("ln -s srma-%s.jar %s/srma.jar" % (version, install_dir))
    sudo("touch %s/env.sh" % install_dir)
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- SRMA %s installed to %s -----" % (version, install_dir)))

def _install_beam():
    version = '2.0'
    url = 'http://www.stat.psu.edu/~yuzhang/software/beam2.tar'
    pkg_name = 'beam'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("tar xf %s" % (os.path.split(url)[-1]))
            install_cmd("mv BEAM2 %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_pass():
    version = '2.0'
    url = 'http://www.stat.psu.edu/~yuzhang/software/pass2.tar'
    pkg_name = 'pass'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("tar xf %s" % (os.path.split(url)[-1]))
            install_cmd("mv pass2 %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_lps_tool():
    version = '2010.09.30'
    url = 'http://www.bx.psu.edu/miller_lab/dist/lps_tool.%s.tar.gz' % version
    pkg_name = 'lps_tool'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("tar zxf %s" % (os.path.split(url)[-1]))
            install_cmd("./lps_tool.%s/MCRInstaller.bin -P bean421.installLocation=\"%s/MCR\" -silent" % (version, install_dir))
            install_cmd("mv lps_tool.%s/lps_tool %s" % (version, install_dir))
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("echo 'MCRROOT=%s/MCR/v711; export MCRROOT' >> %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_plink():
    version = '1.07'
    url = 'http://pngu.mgh.harvard.edu/~purcell/plink/dist/plink-%s-x86_64.zip' % version
    pkg_name = 'plink'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("unzip %s" % (os.path.split(url)[-1]))
            install_cmd("mv plink-%s-x86_64/plink %s" % (version, install_dir))
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_fbat():
    version = '2.0.3'
    url = 'http://www.biostat.harvard.edu/~fbat/software/fbat%s_linux64.tar.gz' % version.replace('.', '')
    pkg_name = 'fbat'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("tar zxf %s" % (os.path.split(url)[-1]))
            install_cmd("mv fbat %s" % install_dir)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_haploview():
    version = '4.2b'
    url = 'http://www.broadinstitute.org/ftp/pub/mpg/haploview/Haploview_beta.jar'
    pkg_name = 'haploview'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            install_cmd("mv %s %s" % (os.path.split(url)[-1], install_dir))
            install_cmd("ln -s %s %s/haploview.jar" % (os.path.split(url)[-1], install_dir))
    sudo("touch %s/env.sh" % install_dir)
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_eigenstrat():
    version = '3.0'
    url = 'http://www.hsph.harvard.edu/faculty/alkes-price/files/EIG%s.tar.gz' % version
    pkg_name = 'eigenstrat'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            run("tar zxf %s" % (os.path.split(url)[-1]))
            install_cmd("mv bin %s" % install_dir)
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_mosaik():
    version = "1.1.0017"
    url = "http://mosaik-aligner.googlecode.com/files/Mosaik-%s-Linux-x64.tar.bz2" % version
    pkg_name = 'mosaik'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s -O %s" % (url, os.path.split(url)[-1]))
            install_cmd("tar -xjvpf %s -C %s" % (os.path.split(url)[-1], install_dir))
    with cd(install_dir):
        with cd("mosaik-aligner"):
            install_cmd("rm -rf data/ MosaikTools/ src/")
        install_cmd("mv mosaik-aligner/* .")
        install_cmd("rm -rf mosaik-aligner")
    install_cmd("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    install_cmd("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    install_cmd('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_freebayes():
    version = "0.6.6" # this seems to be bogus at this point considering it's repo
    url = "git://github.com/ekg/freebayes.git"
    pkg_name = 'freebayes'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            install_cmd("git clone %s" % url)
            with cd("freebayes"):
                install_cmd("make")
                install_cmd("mv bin/* %s" % install_dir)
    install_cmd("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    install_cmd("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    install_cmd('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_picard():
    version = '1.45'
    mirror_info = "?use_mirror=voxel"
    url = 'http://downloads.sourceforge.net/project/picard/picard-tools/%s/picard-tools-%s.zip' \
            % (version, version)
    pkg_name = 'picard'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s%s -O %s" % (url, mirror_info, os.path.split(url)[-1]))
            run("unzip %s" % (os.path.split(url)[-1]))
            install_cmd("mv picard-tools-%s/*.jar %s" % (version, install_dir))
    sudo("touch %s/env.sh" % install_dir)
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- Picard %s installed to %s -----" % (version, install_dir)))

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


## =========== Helpers ==============
def _check_version():
    version = env.version
    if int(version.split(".")[0]) < 1:
        raise NotImplementedError("Please install Fabric version 1.0 or later.")
