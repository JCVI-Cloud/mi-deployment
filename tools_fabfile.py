"""Fabric (http://docs.fabfile.org) deployment file to set up a range of NGS tools

Usage:
    fab -f tools_fabfile.py -i full_path_to_private_key_file -H <servername> install_tools
"""
# for Python 2.5
from __future__ import with_statement

import os
import time
import datetime as dt
from contextlib import contextmanager, nested

# from fabric.api import *
# from fabric.contrib.files import *
from fabric.api import sudo, run, env, cd
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, settings, hide
from fabric.colors import green, yellow, red

# -- Adjust this link if using content from another location
CDN_ROOT_URL = "http://userwww.service.emory.edu/~eafgan/content"

# -- General environment setup
env.user = 'ubuntu'
env.use_sudo = True
env.cloud = False # Flag to indicate if running on a cloud deployment

# -- Provide methods for easy switching between specific environment setups for 
# different deployment scenarios (an environment must be loaded as the first line
# in any invokable function)
def _amazon_ec2_environment():
    """Environment setup for Galaxy on Ubuntu 10.04 on EC2
    Use this environment as a template 
    NOTE: This script/environment assumes given environment directories are available.
    Typically, this would assume starting an EC2 instance, attaching an EBS
    volume to it, creating a file system on it, and mounting it at below paths.
    """
    env.user = 'ubuntu'
    env.galaxy_user = 'galaxy'
    env.install_dir = '/mnt/galaxyTools/tools' # Install all tools under this dir
    env.galaxy_home = '/mnt/galaxyTools/galaxy-central' # Where Galaxy is/will be installed
    env.galaxy_loc_files = '/mnt/galaxyIndices/galaxy/galaxy-data' # Where Galaxy's .loc files are stored
    env.update_default = True # If True, set the tool's `default` directory to point to the tool version currently being installed
    env.tmp_dir = "/mnt"
    env.shell = "/bin/bash -l -c"
    env.use_sudo = True
    env.cloud = True
    print(yellow("Loaded Amazon EC2 environment"))

# -- Fabric instructions

def install_tools():
    """Deploy a Galaxy server along with some tools.
    """
    _check_fabric_version()
    ok = True # Flag indicating if the process is coming along fine
    time_start = dt.datetime.utcnow()
    print(yellow("Configuring host '%s'. Start time: %s" % (env.hosts[0], time_start)))
    _amazon_ec2_environment()
    # Need to ensure the install dir exists and is owned by env.galaxy_user
    if not exists(env.install_dir):
        sudo("mkdir -p %s" % env.install_dir)
        sudo("chown %s %s" % (env.galaxy_user, os.path.split(env.install_dir)[0]))
    _required_packages()
    # _required_libraries() # currently, nothing there
    # _support_programs() # currently, nothing there
    _install_tools()
    answer = confirm("Would you like to install Galaxy?")
    if answer:
        ok = _install_galaxy()
    if env.user != env.galaxy_user and env.use_sudo and ok:
        # Ensure that everything under install dir is owned by env.galaxy_user
        sudo("chown --recursive %s:%s %s" % (env.galaxy_user, env.galaxy_user, os.path.split(env.install_dir)[0]))
        sudo("chmod 755 %s" % os.path.split(env.install_dir)[0])
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
    """Installs packages required to build the tools"""
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
    """ Used to install Galaxy and setup its environment.
    This method cannot be used to update an existing instance of Galaxy code; see
    volume_manipulations_fab.py script for that functionality.
    Also, this method is somewhat targeted for the EC2 deployment so some tweaking
    of the code may be desirable."""
    # MP: we need to have a tmp directory available if files already exist in the galaxy install directory
    install_cmd = sudo if env.use_sudo else run
    tmp_dir = os.path.join(env.tmp_dir, "fab_tmp")
    if exists(tmp_dir):
        install_cmd("rm -rf %s" % tmp_dir)
    if exists(env.galaxy_home):
        if exists(os.path.join(env.galaxy_home, '.hg')):
            print(red("Galaxy install dir '%s' exists and seems to have a Mercurial repository already there. Is Galaxy already installed? Exiting.") % env.galaxy_home)
            return False
        else:
            if not confirm("Galaxy install dir '%s' already exists. Are you sure you want to try to install Galaxy here?" % env.galaxy_home):
                return True
            # MP: need to move any files already in galaxy home so that hg can checkout files.
            if not exists(tmp_dir):
                install_cmd("mkdir %s" % tmp_dir)
                install_cmd("chown %s %s" % (env.user, tmp_dir))
            install_cmd("mv %s/* %s" % (env.galaxy_home, tmp_dir))
    with cd(os.path.split(env.galaxy_home)[0]):
        #MP needs to be done as non galaxy user, otherwise we have a permissions problem.
        sudo('hg clone https://bitbucket.org/galaxy/galaxy-central/')
    # MP: now we need to move the files back into the galaxy directory.
    if exists(tmp_dir):
        install_cmd("cp -R %s/* %s" % (tmp_dir, env.galaxy_home))
        install_cmd("rm -rf %s" % tmp_dir)
    # MP: Ensure that everything under install dir is owned by env.galaxy_user
    sudo("chown --recursive %s:%s %s" % (env.galaxy_user, env.galaxy_user, os.path.split(env.install_dir)[0]))
    sudo("chmod 755 %s" % os.path.split(env.install_dir)[0])
    
    with cd(env.galaxy_home):# and settings(warn_only=True):
        # Make sure Galaxy runs in a new shell and does not inherit the environment
        # by adding the '-ES' flag to all invocations of python within run.sh
        sudo("sed -i 's/python .\//python -ES .\//g' run.sh", user=env.galaxy_user)
        if env.cloud:
            # Append DRMAA_LIBRARY_PATH in run.sh as well (this file will exist
            # once SGE is installed - which happens at instance contextualization)
            sudo("grep -q 'export DRMAA_LIBRARY_PATH=/opt/sge/lib/lx24-amd64/libdrmaa.so.1.0' run.sh; if [ $? -eq 1 ]; then sed -i '2 a export DRMAA_LIBRARY_PATH=/opt/sge/lib/lx24-amd64/libdrmaa.so.1.0' run.sh; fi", user=env.galaxy_user)
            # Upload the custom cloud welcome screen files
            if not exists("%s/static/images/cloud.gif" % env.galaxy_home):
                sudo("wget --output-document=%s/static/images/cloud.gif %s/cloud.gif" % (env.galaxy_home, CDN_ROOT_URL), user=env.galaxy_user)
            if not exists("%s/static/images/cloud_txt.png" % env.galaxy_home):
                sudo("wget --output-document=%s/static/images/cloud_text.png %s/cloud_text.png" % (env.galaxy_home, CDN_ROOT_URL), user=env.galaxy_user)
            sudo("wget --output-document=%s/static/welcome.html %s/welcome.html" % (env.galaxy_home, CDN_ROOT_URL), user=env.galaxy_user)
        # Set up the symlink for SAMTOOLS (remove this code once SAMTOOLS is converted to data tables)
        if exists("%s/tool-data/sam_fa_indices.loc" % env.galaxy_home):
            sudo("rm %s/tool-data/sam_fa_indices.loc" % env.galaxy_home, user=env.galaxy_user)
        tmp_loc = False
        if not exists("%s/sam_fa_indices.loc" % env.galaxy_loc_files):
            sudo("touch %s/sam_fa_indices.loc" % env.galaxy_loc_files, user=env.galaxy_user)
            tmp_loc = True
        sudo("ln -s %s/sam_fa_indices.loc %s/tool-data/sam_fa_indices.loc" % (env.galaxy_loc_files, env.galaxy_home), user=env.galaxy_user)
        if tmp_loc:
            sudo("rm %s/sam_fa_indices.loc" % env.galaxy_loc_files, user=env.galaxy_user)
        # set up the special HYPHY link in tool-data/
        hyphy_dir = os.path.join(env.install_dir, 'hyphy', 'default')
        sudo('ln -s %s tool-data/HYPHY' % hyphy_dir, user=env.galaxy_user)
        # set up the jars directory for Java tools
        if not exists('tool-data/shared/jars'):
            sudo("mkdir -p tool-data/shared/jars", user=env.galaxy_user)
        srma_dir = os.path.join(env.install_dir, 'srma', 'default')
        haploview_dir = os.path.join(env.install_dir, 'haploview', 'default')
        picard_dir = os.path.join(env.install_dir, 'picard', 'default')
        sudo('ln -s %s/srma.jar tool-data/shared/jars/.' % srma_dir, user=env.galaxy_user)
        sudo('ln -s %s/haploview.jar tool-data/shared/jars/.' % haploview_dir, user=env.galaxy_user)
        sudo('ln -s %s/*.jar tool-data/shared/jars/.' % picard_dir, user=env.galaxy_user)
    return True
    
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
    _install_fastqc()

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
                    install_cmd("mv -f %s %s" % (fname.strip(), install_dir))
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
    version = "0.7.0"
    vext = "a"
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- BFAST %s installed to %s -----" % (version, install_dir)))

# @_if_not_installed("ABYSS")
def _install_abyss():
    version = "1.3.1"
    url = "http://www.bcgsc.ca/downloads/abyss/abyss-%s.tar.gz" % version
    pkg_name = 'abyss'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            run("wget %s" % url)
            run("tar -xvzf %s" % (os.path.split(url)[-1]))
            install_cmd = sudo if env.use_sudo else run
            with cd("abyss-%s" % version):
                # Get boost first
                run("wget http://downloads.sourceforge.net/project/boost/boost/1.47.0/boost_1_47_0.tar.bz2")
                run("tar jxf boost_1_47_0.tar.bz2")
                run("ln -s boost_1_47_0/boost boost")
                run("rm boost_1_47_0.tar.bz2")
                # Get back to abyss
                run("./configure --prefix=%s --with-mpi=/opt/galaxy/pkg/openmpi" % install_dir)
                run("make")
                install_cmd("make install")
    sudo("echo 'PATH=%s/bin:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- ABySS %s installed to %s -----" % (version, install_dir)))

def _install_velvet():
    version = "1.1.06"
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
                    with settings(warn_only=True):
                        tmp_cmd = "mv -f %s %s" % (fname, install_dir)
                        print "tmp_cmd: %s" % tmp_cmd
                        install_cmd(tmp_cmd)
    sudo("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    sudo("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- Velvet %s installed to %s -----" % (version, install_dir)))

def _install_macs():
    version = "1.4.1"
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- MACS %s installed to %s -----" % (version, install_dir)))

def _install_tophat():
    version = '1.3.3'
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- TopHat %s installed to %s -----" % (version, install_dir)))

def _install_cufflinks():
    version = '1.1.0'
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
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
    version = '1.2-65-ge4a583a'
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    # Link jar to Galaxy's jar dir
    jar_dir = os.path.join(env.galaxy_home, 'tool-data', 'shared', 'jars', pkg_name)
    if not exists(jar_dir):
        install_cmd("mkdir -p %s" % jar_dir)
    tool_dir = os.path.join(env.install_dir, pkg_name, 'default', 'bin')
    install_cmd('ln --force --symbolic %s/*.jar %s/.' % (tool_dir, jar_dir))
    install_cmd('chown --recursive %s:%s %s' % (env.galaxy_user, env.galaxy_user, jar_dir))
    print(green("----- GATK %s installed to %s -----" % (version, install_dir)))

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
    version = "1.1.0021"
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_freebayes():
    version = time.strftime("%Y-%m-%d") # set version to today's date considering it's a repo
    url = "git://github.com/ekg/freebayes.git"
    pkg_name = 'freebayes'
    install_dir = os.path.join(env.install_dir, pkg_name, version)
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            install_cmd("git clone --recursive %s" % url)
            with cd("freebayes"):
                install_cmd("make")
                install_cmd("mv bin/* %s" % install_dir)
    install_cmd("echo 'PATH=%s:$PATH' > %s/env.sh" % (install_dir, install_dir))
    install_cmd("chmod +x %s/env.sh" % install_dir)
    install_dir_root = os.path.join(env.install_dir, pkg_name)
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    print(green("----- %s %s installed to %s -----" % (pkg_name, version, install_dir)))

def _install_picard():
    version = '1.55'
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
    if env.update_default:
        sudo('ln --symbolic --no-dereference --force %s %s/default' % (install_dir, install_dir_root))
    else:
        sudo('if [ ! -d %s/default ]; then ln -s %s %s/default; fi' % (install_dir_root, install_dir, install_dir_root))
    # set up the jars directory
    jar_dir = os.path.join(env.galaxy_home, 'tool-data', 'shared', 'jars', 'picard')
    if not exists(jar_dir):
        install_cmd("mkdir -p %s" % jar_dir)
    tool_dir = os.path.join(env.install_dir, pkg_name, 'default')
    install_cmd('ln --force --symbolic %s/*.jar %s/.' % (tool_dir, jar_dir))
    install_cmd('chown --recursive %s:%s %s' % (env.galaxy_user, env.galaxy_user, jar_dir))
    print(green("----- Picard %s installed to %s and linked to %s -----" % (version, install_dir, jar_dir)))

def _install_fastqc():
    """ This tool is installed in Galaxy's jars dir """
    version = '0.10.0'
    url = 'http://www.bioinformatics.bbsrc.ac.uk/projects/fastqc/fastqc_v%s.zip' % version
    pkg_name = 'FastQC'
    install_dir = os.path.join(env.galaxy_home, 'tool-data', 'shared', 'jars')
    install_cmd = sudo if env.use_sudo else run
    if not exists(install_dir):
        install_cmd("mkdir -p %s" % install_dir)
    with cd(install_dir):
        install_cmd("wget %s -O %s" % (url, os.path.split(url)[-1]))
        install_cmd("unzip %s" % (os.path.split(url)[-1]))
        install_cmd("rm %s" % (os.path.split(url)[-1]))
        with cd(pkg_name):
            install_cmd('chmod 755 fastqc')
        install_cmd('chown --recursive %s:%s %s' % (env.galaxy_user, env.galaxy_user, pkg_name))
    print(green("----- FastQC v%s installed to %s -----" % (version, install_dir)))

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
def _check_fabric_version():
    version = env.version
    if int(version.split(".")[0]) < 1:
        raise NotImplementedError("Please install Fabric version 1.0 or later.")
